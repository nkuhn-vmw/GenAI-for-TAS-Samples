# Import Application Dependencies
import streamlit as st
from langchain_community.llms import OpenAI
from streamlit_chat import message
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.embeddings import LocalAIEmbeddings
from langchain.vectorstores.pgvector import PGVector
from langchain_community.chat_models import ChatOpenAI
from langchain.prompts.prompt import PromptTemplate
from langchain.chains import ConversationalRetrievalChain
from langchain.docstore.document import Document
import psycopg2
import os
import pathlib
import httpx
import asyncio
from concurrent.futures import Future
from threading import Thread
from langchain.document_loaders.csv_loader import CSVLoader
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from cfenv import AppEnv

### Define Global Variables

env = AppEnv()
pg = env.get_service(label='postgres')
llm = env.get_service(label='genai')
chunk_size = 500
chunk_overlap = 50
database_name = pg.credentials['db']
username = pg.credentials['user']
password = pg.credentials['password']
db_host = pg.credentials['hosts'][0]
db_port = pg.credentials['port']
api_key = llm.credentials['api_key']
api_base = llm.credentials['api_base']
cwd = os.getcwd()
# Define the collection name for the vector store
collection_name = "test1"
# Create a connection string for the vector database
connection_string = PGVector.connection_string_from_db_params(
    driver="psycopg2",
    host=db_host,
    port=int(db_port),
    database=database_name,
    user=username,
    password=password,
)

platform_certs = "/etc/ssl/certs/ca-certificates.crt"
os.environ["REQUESTS_CA_BUNDLE"] = platform_certs
os.environ["SSL_CERT_FILE"] = platform_certs

### Define application functions

def setup_database():
    conn = psycopg2.connect(database=database_name, user=username, password=password, host=db_host, port=db_port)
    conn.autocommit = True
    
    with conn.cursor() as c:
        c.execute(f"DROP TABLE IF EXISTS {collection_name}")
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")

    conn.close()

def get_file_extension(uploaded_file):
    file_extension = os.path.splitext(uploaded_file)[1].lower()
    return file_extension

def save_uploadedfile(uploadedfile):
    with open(os.path.join(cwd, uploadedfile.name), "wb") as f:
        f.write(uploadedfile.getbuffer())
    return uploadedfile.name

def read_and_textify(file):
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=2000,
        chunk_overlap=100,
        length_function=len,
    )
    tmp_file_path = save_uploadedfile(file)
    file_extension = get_file_extension(tmp_file_path)
    metadata = {"source": file.name}
    if file_extension == ".csv":
        loader = CSVLoader(file_path=tmp_file_path, csv_args={
            'delimiter': ',', })
        data = loader.load()
    elif file_extension == ".pdf":
        loader = PyPDFLoader(file_path=tmp_file_path)
        data = loader.load_and_split(text_splitter)
    elif file_extension == ".txt":
        loader = TextLoader(file_path=tmp_file_path)
        data = loader.load_and_split(text_splitter)

    for doc in data:
        doc.metadata = metadata  # Assign metadata to each document

    return data

async def create_embeddings_async(documents):
    async with httpx.AsyncClient() as client:
        class AsyncOpenAIEmbeddings(OpenAIEmbeddings):
            async def _embed(self, text: str, *, engine: str):
                response = await client.post(
                    f"{self.openai_api_base}/api/embeddings",
                    headers={"Authorization": f"Bearer {self.openai_api_key}"},
                    json={
                        "input": text,
                        "engine": engine,
                    },
                )
                response.raise_for_status()
                return response.json()["data"][0]["embedding"]

        embeddings = AsyncOpenAIEmbeddings(model=os.environ["EMBEDDING_MODEL"], openai_api_key=api_key, openai_api_base=api_base)
        PGVector.from_documents(documents, embeddings, 
                                collection_name=collection_name,
                                connection_string=connection_string,
                                use_jsonb=True)

def run_async(func, future, args):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    if args is None:
        result = loop.run_until_complete(func())
    else:
        result = loop.run_until_complete(func(args))
    future.set_result(result)

def create_embeddings(documents):
    future = Future()
    Thread(target=run_async, args=(create_embeddings_async, future, documents)).start()
    return future.result()

async def create_retriever_async():
    async with httpx.AsyncClient() as client:
        class AsyncOpenAIEmbeddings(OpenAIEmbeddings):
            async def _embed(self, text: str, *, engine: str):
                response = await client.post(
                    f"{self.openai_api_base}/api/embeddings",
                    headers={"Authorization": f"Bearer {self.openai_api_key}"},
                    json={
                        "input": text,
                        "engine": engine,
                    },
                )
                response.raise_for_status()
                return response.json()["data"][0]["embedding"]

        embeddings = AsyncOpenAIEmbeddings(model=os.environ["EMBEDDING_MODEL"], openai_api_key=api_key, openai_api_base=api_base)
        vStore = PGVector(
            collection_name=collection_name,
            connection_string=connection_string,
            embedding_function=embeddings,
            use_jsonb=True,
        )
        retriever = vStore.as_retriever()
        retriever.search_kwargs = {'k': 2}
        return retriever

def create_retriever():
    future = Future()
    Thread(target=run_async, args=(create_retriever_async, future, None)).start()
    return future.result()

def upload():
    if uploaded_file is None:
        st.session_state["upload_state"] = "Upload a file first!"
    else:
        documents = read_and_textify(uploaded_file)
        create_embeddings(documents)
        st.session_state["upload_state"] = uploaded_file.name + " loaded.."

### Execute application control flow

# Set up the database
setup_database()

# Create the retriever
retriever = create_retriever()

# Initialize the language model
llm = ChatOpenAI(model_name=os.environ["INFERENCE_MODEL"], openai_api_key=api_key, openai_api_base=api_base, streaming=True)

# Define the prompt templates
qa_template = """
    Conversational TAS Bot
    Context: {context}
    =========
    Question: {question}
    ======
    """
qa_prompt = PromptTemplate(template=qa_template, input_variables=["context", "question"])

# Create the conversational retrieval chain
chain = ConversationalRetrievalChain.from_llm(
    llm=llm,
    retriever=retriever,
    verbose=True,
    return_source_documents=True,
    max_tokens_limit=4097,
    combine_docs_chain_kwargs={'prompt': qa_prompt}
)


if 'history' not in st.session_state:
    st.session_state['history'] = []

if 'generated' not in st.session_state:
    st.session_state['generated'] = ["Hello ! Ask me anything!"]
if 'past' not in st.session_state:
    st.session_state['past'] = ["Hey ! 👋"]

st.write("---")
st.title('Tanzu GPT: ChatBot with pgvector embeddings')

# File uploader
uploaded_file = st.file_uploader("Upload document", type=["txt"])
upload_state = st.text_area("Upload State", "", key="upload_state")

st.button("Upload file", on_click=upload)

# Create containers for the response and input form
response_container = st.container()
container = st.container()

with container:
    with st.form(key='my_form', clear_on_submit=True):
        user_q = st.text_area("Enter your questions here")
        submit_button = st.form_submit_button(label='Send')

    if submit_button and user_q:
        chain_input = {"question": user_q, "chat_history": st.session_state["history"]}
        result = chain(chain_input)
        st.session_state["history"].append((user_q, result["answer"]))
        st.session_state['past'].append(user_q)
        st.session_state['generated'].append(result["answer"])

with response_container:
    for i in range(len(st.session_state['generated'])):
        message(st.session_state["past"][i], is_user=True, key=str(i) + '_user', avatar_style="big-smile")
        message(st.session_state["generated"][i], key=str(i), avatar_style="thumbs")