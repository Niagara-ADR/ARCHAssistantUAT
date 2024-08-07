import streamlit as st
from openai import OpenAI
import requests
from datetime import timedelta, datetime
import pandas as pd
import json
import time
from dateutil.relativedelta import relativedelta
import re
import pytz

  
st.title("ARCH UAT Ticket Assistant")

openai_api_key = st.secrets.openai_api_key

client = OpenAI(api_key=openai_api_key)

class AssistantManager:
    assistant_id = st.secrets.assistant_id
    vector_store_id = st.secrets.vector_store_id

    def __init__(self):
        self.client = client
        self.model = "gpt-4o"
        self.thread = None
        self.run = None
        self.vector_store = self.client.beta.vector_stores.retrieve(
            vector_store_id=AssistantManager.vector_store_id
        )
        self.assistant = self.client.beta.assistants.retrieve(
            assistant_id=AssistantManager.assistant_id
        )

    def get_ticket_details(self, start_date=None, end_date=None, date_string=None):
        if not start_date and not end_date:
            if not date_string:
                date_string = "recent"
            today = datetime.today()
            if date_string == "past month":
                start_date = today - timedelta(days=30)
                end_date = today
            elif date_string == "last year":
                start_date = today.replace(year=today.year - 1, month=1, day=1)
                end_date = today.replace(year=today.year - 1, month=12, day=31)
            elif date_string == "this year" or date_string == "past year":
                start_date = today.replace(month=1, day=1)
                end_date = today
            elif date_string == "recent" or date_string == "past week" or date_string == "this year":
                start_date = today - timedelta(days=7)
                end_date = today
            else:
                match_days = re.match(r'last (\d+) days', date_string)
                match_weeks = re.match(r'last (\d+) weeks', date_string)
                match_months = re.match(r'last (\d+) months', date_string)
                
                if match_days:
                    days = int(match_days.group(1))
                    start_date = today - timedelta(days=days)
                    end_date = today
                elif match_weeks:
                    weeks = int(match_weeks.group(1))
                    start_date = today - timedelta(weeks=weeks)
                    end_date = today
                elif match_months:
                    months = int(match_months.group(1))
                    start_date = today - relativedelta(months=months)
                    end_date = today
                else:
                    start_date = today - timedelta(days=7)
                    end_date = today
            start_date = start_date.strftime('%Y-%m-%d')
            end_date = end_date.strftime('%Y-%m-%d')


        url = st.secrets.token_url
        payload = {
            "grant_type": "client_credentials",
            "client_id": st.secrets.client_id,
            "client_secret": st.secrets.client_secret,
        }

        response = requests.post(url, data=payload)

        access_token = response.json().get('access_token')

        headers = {
                'Authorization': 'Bearer:' + access_token,
                'Content-Type': 'application/json'
            }
        if not start_date or datetime.strptime(start_date, "%Y-%m-%d").date() > datetime.now().date():
            start_date = (datetime.now().date() - timedelta(days=7)).strftime('%Y-%m-%d')
        if not end_date or datetime.strptime(end_date, "%Y-%m-%d").date() > datetime.now().date():
            end_date=(datetime.now().date()).strftime('%Y-%m-%d')
        print(start_date)
        print(end_date)
        response = requests.get('https://uat-archapi.niagarawater.com/api/tickets/getAllTicketDetails?usecase_id=["60cb784aafe4530011138ca9"]&start_date={}&end_date={}'.format(start_date, end_date), headers = headers)
        json = response.json()
        df = pd.DataFrame(json['response'])
        df_plants = pd.read_excel('Plant Acronyms.xlsx')

        location_dict = pd.Series(df_plants['Location Name'].values, index=df_plants['Abbreviation']).to_dict()
        org_code_dict = pd.Series(df_plants['Organization Code'].values, index=df_plants['Abbreviation']).to_dict()

        def calculate_duration(row):
            ticket_creation_date = datetime.fromisoformat(row['ticket_creation_date'].replace('Z', '+00:00'))
            ticket_creation_date = ticket_creation_date.astimezone(pytz.UTC) 
            
            if row['closedDate'] != 'Cannot be determined':
                try:
                    closed_date = datetime.fromisoformat(row['closedDate'].replace('Z', '+00:00'))
                    closed_date = closed_date.astimezone(pytz.UTC)
                    duration = closed_date - ticket_creation_date
                except ValueError:
                    duration = datetime.now(pytz.UTC) - ticket_creation_date 
            else:
                duration = datetime.now(pytz.UTC) - ticket_creation_date 
            
            days = duration.days
            hours = duration.seconds // 3600
            return f"{days} days {hours} hours"

        df['plant_acronym'] = df['ticket_id'].str[:3]
        df['location_name'] = df['plant_acronym'].map(location_dict)
        df['organization_code'] = df['plant_acronym'].map(org_code_dict)
        df = df.drop(columns=['plant_acronym'])
        df['duration'] = df.apply(calculate_duration, axis=1)
        
        try:
            df = df.drop('_id', axis=1)
            df = df.drop('lastest_Message', axis=1)
            df = df.drop('comments', axis=1)
            df.sort_values(by='ticket_creation_date', ascending=False, inplace=True)
        except:
            pass
        print(df)
        return df.to_json(orient='index')

    
    def create_thread(self):
        if not self.thread:
            thread_obj = self.client.beta.threads.create()
            self.thread = thread_obj
            print(f"CREATED NEW THREAD: {self.thread.id}")

  
    def add_message_to_thread(self, role, content):
        if self.thread:
            self.client.beta.threads.messages.create(
                thread_id=self.thread.id, role=role, content=content
            )

    def run_assistant(self):
        if self.thread and self.assistant:
            self.run = self.client.beta.threads.runs.create(
                thread_id=self.thread.id,
                assistant_id=self.assistant.id,
            )
    
    def process_message(self):
        if self.thread:
            messages = self.client.beta.threads.messages.list(thread_id=self.thread.id)
            summary = []
            last_message = messages.data[0]
            response = last_message.content[0].text.value
            summary.append(response)

            self.summary = "\n".join(summary)
            return response

    def call_required_functions(self, required_actions):
        if not self.run:
            return
        tool_outputs = []
        for action in required_actions["tool_calls"]:
            func_name = action["function"]["name"]
            arguments = json.loads(action['function']['arguments'])
            if func_name == "get_ticket_details":
                output = self.get_ticket_details(start_date=arguments.get("start_date"), end_date=arguments.get("end_date"), date_string=arguments.get("date_string"))
                tool_outputs.append({"tool_call_id" : action["id"], "output" : output})
            else:
                raise ValueError(f"Unknown function: {func_name}")
        
        print(tool_outputs)

        self.client.beta.threads.runs.submit_tool_outputs(
            thread_id=self.thread.id, run_id=self.run.id, tool_outputs=tool_outputs
        )
      
    def wait_for_completion(self):
        if self.thread and self.run:
            while True:
                time.sleep(1)
                run_status = self.client.beta.threads.runs.retrieve(
                    thread_id=self.thread.id, run_id=self.run.id
                )
                print(run_status)
                if run_status.status == "completed":
                    return self.process_message()
                elif run_status.status == "requires_action":
                    self.call_required_functions(
                        required_actions=run_status.required_action.submit_tool_outputs.model_dump()
                    )
                elif run_status.status == "failed":
                    return run_status.status

    def run_steps(self):
        run_steps = self.client.beta.threads.runs.steps.list(
            thread_id=self.thread.id, run_id=self.run.id
        )
        print(f"Run-Steps::: {run_steps}")
        return run_steps.data

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if "assistant_manager" not in st.session_state:
    st.session_state.assistant_manager = AssistantManager()
    st.session_state.assistant_manager.create_thread()

manager = st.session_state.assistant_manager

if prompt := st.chat_input("Message ARCH API Assistant"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    manager.add_message_to_thread(role="user", content=prompt)
    with st.chat_message("user"):
        st.markdown(prompt)
    manager.run_assistant()
    response = manager.wait_for_completion()
    print(response)

    with st.chat_message("assistant"):
        st.write(response)

    st.session_state.messages.append({"role": "assistant", "content": response})

uploaded_file = st.file_uploader("Add an attachment", type=["pdf", "jpg", "png", "docx"])

if uploaded_file is not None:
    st.success(f"File {uploaded_file.name} uploaded successfully!")