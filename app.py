from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory
from flask_session import Session
import mysql.connector
import os
import shutil
from werkzeug.utils import secure_filename
import uuid
import logging
import nltk
import spacy
from transformers import T5Tokenizer, T5ForConditionalGeneration
import torch

# Configure logging
logging.basicConfig(level=logging.INFO, filename='app.log', format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Ensure NLTK resources
def ensure_nltk_resources():
    resources = ['punkt', 'punkt_tab']
    for resource in resources:
        try:
            nltk.data.find(f'tokenizers/{resource}')
            logger.info(f"NLTK resource '{resource}' already downloaded.")
        except LookupError:
            logger.info(f"Downloading NLTK resource '{resource}'...")
            nltk.download(resource, quiet=False)
            logger.info(f"Successfully downloaded '{resource}'.")

# Initialize Flask app
app = Flask(__name__)
app.secret_key = 'your-secret-key-here'
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

# Load spaCy model
nlp = spacy.load("en_core_web_sm")

# Load fine-tuned T5 model
tokenizer = T5Tokenizer.from_pretrained("./t5_finetuned")
model = T5ForConditionalGeneration.from_pretrained("./t5_finetuned")

UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

DOWNLOAD_FOLDER = 'downloads'
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)
app.config['DOWNLOAD_FOLDER'] = DOWNLOAD_FOLDER

def create_connection():
    try:
        return mysql.connector.connect(
            host='localhost',
            database='lisa20db',
            user='root',
            password=''  # Update with your MySQL password
        )
    except mysql.connector.Error as e:
        logger.error(f"Database connection failed: {e}")
        raise

def process_uploads(files):
    upload_info = []
    for file in files:
        if file.filename == '':
            continue
        safe_filename = secure_filename(file.filename)
        unique_name = f"{uuid.uuid4().hex}_{safe_filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        file.save(file_path)
        upload_info.append(f"Saved: {unique_name}")
        logger.info(f"Uploaded file: {unique_name}")
    return "\nUploaded Files:\n" + "\n".join(upload_info) if upload_info else ""

def traverse_folder(folder_path, connection, cursor):
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            file_path = os.path.join(root, file)
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            relative_path = os.path.relpath(file_path, folder_path)
            cursor.execute('''INSERT INTO learning_data (example_code, language, description, tags) 
                              VALUES (%s, %s, %s, %s)''', 
                           (content, 'text', relative_path, 'folder_structure'))
            connection.commit()
            logger.info(f"Processed file: {file_path}")

def create_downloadable_folder(folder_path, connection, cursor):
    cursor.execute("SELECT example_code, description FROM learning_data WHERE tags LIKE %s", ('%folder_structure%',))
    data = cursor.fetchall()
    for content, relative_path in data:
        file_path = os.path.join(folder_path, relative_path)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"Created file: {file_path}")

def learn_from_chat(user_message, system_response, connection, cursor):
    if "I'm still learning" in system_response or "```" in system_response:
        language = "Chat"
        description = f"Learned from user message: {user_message}"
        tags = "chat,auto-learned,folder_structure"
        cursor.execute('''INSERT INTO learning_data (example_code, language, description, tags) 
                          VALUES (%s, %s, %s, %s)''', 
                       (system_response, language, description, tags))
        connection.commit()
        logger.info(f"Learned from chat: {description}")
        return cursor.lastrowid
    return None

def list_files_and_folders(folder_path):
    files = []
    folders = []
    for root, dirs, file_names in os.walk(folder_path):
        for file_name in file_names:
            files.append(os.path.join(root, file_name))
        for dir_name in dirs:
            folders.append(os.path.join(root, dir_name))
    return files, folders

def count_files_and_folders(folder_path):
    files, folders = list_files_and_folders(folder_path)
    return len(files), len(folders), len(os.listdir(folder_path))

@app.route('/home', methods=['GET', 'POST'])
def home():
    connection = create_connection()
    cursor = connection.cursor()
    try:
        if request.method == 'POST':
            uploaded_files = request.files.getlist('attachments')
            upload_result = process_uploads(uploaded_files)
            if upload_result:
                folder_path = os.path.join(app.config['UPLOAD_FOLDER'], uploaded_files[0].filename)
                num_files, num_folders, num_subfolders = count_files_and_folders(folder_path)
                return render_template('upload_progress.html', folder_path=folder_path, num_files=num_files, num_folders=num_folders, num_subfolders=num_subfolders)
            return redirect(url_for('index'))
        return render_template('home.html')
    except Exception as e:
        logger.error(f"Error in home: {str(e)}")
        return "Server error", 500
    finally:
        connection.close()

@app.route('/upload_progress/<path:folder_path>')
def upload_progress(folder_path):
    num_files, num_folders, num_subfolders = count_files_and_folders(folder_path)
    return render_template('upload_progress.html', folder_path=folder_path, num_files=num_files, num_folders=num_folders, num_subfolders=num_subfolders)

@app.route('/continue_learning', methods=['POST'])
def continue_learning():
    connection = create_connection()
    cursor = connection.cursor()
    try:
        folder_path = os.path.join(app.config['DOWNLOAD_FOLDER'], 'downloadable_folder')
        create_downloadable_folder(folder_path, connection, cursor)
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Error in continue_learning: {str(e)}")
        return "Server error", 500
    finally:
        connection.close()

@app.route('/', methods=['GET', 'POST'])
def index():
    connection = create_connection()
    cursor = connection.cursor()
    try:
        if request.method == 'GET':
            session['messages'] = []
            session.modified = True

        if request.method == 'POST':
            if 'user_message' in request.form:
                user_message = request.form['user_message']
                uploaded_files = request.files.getlist('attachments')
                upload_result = process_uploads(uploaded_files)

                doc = nlp(user_message.lower())
                keywords = [token.text for token in doc if not token.is_stop and token.is_alpha]
                query = " OR ".join(["example_code LIKE %s OR description LIKE %s OR tags LIKE %s"] * len(keywords))
                params = [f"%{kw}%" for kw in keywords for _ in range(3)]
                cursor.execute(f"SELECT id, example_code, language, description, rating FROM learning_data WHERE {query}", params)
                relevant_examples = cursor.fetchall()

                if relevant_examples:
                    best_example = max(relevant_examples, key=lambda x: x[4] or 0)
                    example_id, example_code, language, description, _ = best_example
                    execution_result = execute_code_sandbox(example_code, language)
                    modified_code = generate_code(user_message, example_code)
                    system_response = f"I found a relevant {language} example:\n<pre><code>{example_code}</code></pre>\nModified:\n<pre><code>{modified_code}</code></pre>"
                    if execution_result:
                        system_response += f"\nExecution Result: {execution_result}"
                    learning_data_id = example_id
                else:
                    if any(word in user_message.lower() for word in ["write", "create", "generate"]):
                        generated_code = generate_code(user_message)
                        system_response = f"Generated code:\n<pre><code>{generated_code}</code></pre>"
                        learning_data_id = None
                    else:
                        system_response = "I'm still learning! Could you provide a code example or more info?"
                        learning_data_id = None
                if upload_result:
                    system_response += upload_result

                cursor.execute('''INSERT INTO messages (user_message, system_response, learning_data_id) 
                                 VALUES (%s, %s, %s)''', 
                              (user_message, system_response, learning_data_id))
                message_id = cursor.lastrowid

                new_learning_id = learn_from_chat(user_message, system_response, connection, cursor)
                if new_learning_id and not learning_data_id:
                    cursor.execute("UPDATE messages SET learning_data_id = %s WHERE id = %s", (new_learning_id, message_id))

                session['messages'].append((message_id, user_message, system_response, None if learning_data_id else None))
                session.modified = True
                connection.commit()
                logger.info(f"Processed message: {user_message}")

            elif 'message_id' in request.form:
                message_id = request.form['message_id']
                rating = int(request.form['rating'])
                cursor.execute("SELECT learning_data_id FROM messages WHERE id = %s", (message_id,))
                learning_data_id = cursor.fetchone()[0]
                if learning_data_id:
                    cursor.execute("UPDATE learning_data SET rating = %s WHERE id = %s", (rating, learning_data_id))
                    connection.commit()
                    session['flash_message'] = "Rating updated!"
                    logger.info(f"Rated message {message_id}: {rating}")

        messages = session.get('messages', [])
        flash_message = session.pop('flash_message', None)
        return render_template('index.html', messages=messages, flash_message=flash_message)
    except Exception as e:
        logger.error(f"Error in index: {str(e)}")
        return "Server error", 500
    finally:
        connection.close()

@app.route('/clear', methods=['POST'])
def clear():
    session['messages'] = []
    session.modified = True
    logger.info("Conversation cleared")
    return redirect(url_for('index'))

if __name__ == "__main__":
    ensure_nltk_resources()
    app.run(debug=True)