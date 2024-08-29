import streamlit as st
from openai import OpenAI
import requests
from datetime import timedelta, datetime
import pandas as pd
import json
import time
import pytz
from typing_extensions import override
from openai import AssistantEventHandler
import threading 
# from streamlit.runtime.scriptrunner import add_script_run_ctx
# from streamlit.runtime.scriptrunner.script_run_context import get_script_run_ctx
# from streamlit.runtime import get_instance
from whisper_stt import whisper_stt

st.title("ARCH UAT Ticket Assistant")

openai_api_key = st.secrets.openai_api_key

client = OpenAI(api_key=openai_api_key)

class EventHandler(AssistantEventHandler):    
  @override
  def on_text_created(self, text) -> None:
    print(f"\nassistant > ", end="", flush=True)
      
  @override
  def on_text_delta(self, delta, snapshot):
    print(delta.value, end="", flush=True)
      
  def on_tool_call_created(self, tool_call):
    print(f"\nassistant > {tool_call.type}\n", flush=True)
  
  def on_tool_call_delta(self, delta, snapshot):
    if delta.type == 'code_interpreter':
      if delta.code_interpreter.input:
        print(delta.code_interpreter.input, end="", flush=True)
      if delta.code_interpreter.outputs:
        print(f"\n\noutput >", flush=True)
        for output in delta.code_interpreter.outputs:
          if output.type == "logs":
            print(f"\n{output.logs}", flush=True)

class AssistantManager:
    assistant_id = st.secrets.assistant_id

    def __init__(self):
        self.client = client
        self.model = "gpt-3.5-turbo"
        self.thread = None
        self.run = None
        self.file = None
        self.assistant = self.client.beta.assistants.retrieve(
            assistant_id=AssistantManager.assistant_id
        )

    def get_ticket_details(self):
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
        
        start_date = (datetime.now().date() - timedelta(weeks=1000)).strftime('%Y-%m-%d')
        end_date=(datetime.now().date()).strftime('%Y-%m-%d')
        print(start_date)
        print(end_date)
        response = requests.get('https://archapi.niagarawater.com/api/tickets/getAllTicketDetails?usecase_id=[]&start_date={}&end_date={}'.format(start_date, end_date), headers = headers)
        json = response.json()
        df = pd.DataFrame(json['response'])
        df_plants = pd.read_excel('Plant Acronyms.xlsx')
        def lower(string):
            string = string.lower()
            return string.capitalize()
        df_plants['Location Name'] = df_plants['Location Name'].map(lower)
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
            df.reset_index(drop=True, inplace=True)
        except:
            pass
        print(df)
        df.to_excel('temp_dev.xlsx', index=False)
        file = client.files.create(
            file=open("temp_dev.xlsx", "rb"),
            purpose='assistants'
        )
        self.file = file
        print(self.file.id)
        thread = client.beta.threads.create(
            messages=[
                {
                "role": "user",
                "content": "Answer questions about tickets using the file.",
                "attachments": [
                    {
                    "file_id": file.id,
                    "tools": [{"type": "code_interpreter"}]
                    }
                ]
                }
            ]
        )
        return thread

    
    def create_thread(self):
        if not self.thread:

            thread_obj = self.get_ticket_details()
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

        with client.beta.threads.runs.stream(
            thread_id=self.thread.id,
            assistant_id=AssistantManager.assistant_id,
            event_handler=EventHandler(),
        ) as stream:
            stream.until_done()

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

    # def start_beating(self, user_id):
    #     thread = threading.Timer(interval=2, function= self.start_beating, args=(user_id,) )

    #     add_script_run_ctx(thread)

    #     ctx = get_script_run_ctx()     

    #     runtime = get_instance()

    #     if runtime.is_active_session(session_id=ctx.session_id):
    #         thread.start()
    #     else:
    #         try:
    #             self.client.files.delete(self.file.id)
    #         except:
    #             pass


if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if "assistant_manager" not in st.session_state:
    st.session_state.assistant_manager = AssistantManager()
    st.session_state.assistant_manager.create_thread()

manager = st.session_state.assistant_manager

# ctx = get_script_run_ctx()

# user_id = ctx.session_id
# manager.start_beating(user_id)
if "initial_question" not in st.session_state:
    manager.add_message_to_thread(role="user", content="Give me a summary of the ARCH tickets.")
    manager.run_assistant()
    response = manager.wait_for_completion()
    st.session_state.initial_question = True

def callback():
    if "my_stt_output" in st.session_state and st.session_state.my_stt_output:
        output = st.session_state.my_stt_output
    else:
        return
    with st.chat_message("user"):
        st.markdown(output)
    st.session_state.messages.append({"role": "user", "content": output})
    manager.add_message_to_thread(role="user", content=output)
    manager.run_assistant()
    response = manager.wait_for_completion()
    print(response)

    with st.chat_message("assistant"):
        st.write(response)

    st.session_state.messages.append({"role": "assistant", "content": response})

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
        # st.write_stream()

    st.session_state.messages.append({"role": "assistant", "content": response})

uploaded_file = st.file_uploader("Add an attachment", type=["pdf", "jpg", "png", "docx"])

whisper_stt(openai_api_key= openai_api_key, language = 'en', callback=callback, key="my_stt")  

if uploaded_file is not None:
    st.success(f"File {uploaded_file.name} uploaded successfully!")
