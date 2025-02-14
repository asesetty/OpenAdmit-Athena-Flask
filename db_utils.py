import json
import os

STUDENTS_JSON_PATH = os.path.join("data", "students.json")
MENTOR_EMBEDDINGS_JSON_PATH = os.path.join("data", "mentor_embeddings.json")

def load_students_data():
    try:
        with open(STUDENTS_JSON_PATH, "r") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except FileNotFoundError:
        return {}

def save_students_data(data):
    with open(STUDENTS_JSON_PATH, "w") as f:
        json.dump(data, f, indent=2)

def load_mentor_embeddings():
    try:
        with open(MENTOR_EMBEDDINGS_JSON_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
