import openai
import numpy as np
from db_utils import load_mentor_embeddings
import re

MENTOR_EMBEDDINGS = load_mentor_embeddings()

MENTOR_RECOMMENDATION_THRESHOLD = 0.3

def generate_embedding(text, model="text-embedding-ada-002"):
    response = openai.embeddings.create(model=model, input=text)
    return response.data[0].embedding

def cosine_similarity(vec1, vec2):
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)
    return float(np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2)))

def should_recommend_mentor(user_message):
    # maybe add NLP later :-(
    return True

def recommend_mentor(user_message, student_info):
    profile_text = (
        f"Name: {student_info.get('name','')}\n"
        f"Grade: {student_info.get('grade','')}\n"
        f"Interests: {student_info.get('hobbies','')}, {student_info.get('favorite_subjects','')}, "
        f"{student_info.get('coursework','')}, {student_info.get('care_about','')}\n"
        f"User Query: {user_message}\n"
    )
    user_vector = generate_embedding(profile_text)

    best_mentor = None
    best_score = 0.0

    for mentor_id, mentor_vec in MENTOR_EMBEDDINGS.items():
        sim = cosine_similarity(user_vector, mentor_vec)
        if sim > best_score:
            best_score = sim
            best_mentor = mentor_id

    if best_score >= MENTOR_RECOMMENDATION_THRESHOLD:
        return best_mentor, best_score
    return None, best_score

def generate_mentor_reason(mentor_id, user_message):
    prompt = (
        "You are a helpful AI. A student asked a question, and we recommended a mentor. "
        f"The mentor's ID is '{mentor_id}'. The student's query: '{user_message}' "
        "Generate a short 1-2 sentence reason referencing the mentor's possible expertise or background. "
        "If you lack details, be generic. Be friendly."
    )

    response = openai.chat.completions.create(
        model="gpt-3.5-turbo",  # or "gpt-4"
        messages=[{"role": "system", "content": prompt}],
        max_tokens=50,
        temperature=0.7
    )
    return response.choices[0].message.content.strip()

MENTOR_REQUEST_EXAMPLES = [
    "Can you recommend a mentor?",
    "Who would be a good mentor for me?",
    "Suggest me a mentor for my interest in cooking",
    "I need help finding a mentor",
    "I want guidance from an expert",
    "Which mentor do you suggest?",
    "Who should mentor me?",
    "Help me connect with a mentor in my field",
]

def get_text_embedding(text):
    """Get OpenAI embedding vector for a given text."""
    try:
        response = openai.embeddings.create(
            model="text-embedding-ada-002",
            input=text
        )
        return np.array(response.data[0].embedding)
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return None

def is_explicit_mentor_request(user_message):
    """Compares user input to predefined mentor request examples using embeddings."""
    user_embedding = get_text_embedding(user_message)
    if user_embedding is None:
        return False  # If embedding fails, fall back to default

    threshold = 0.85  # Define similarity threshold

    for example in MENTOR_REQUEST_EXAMPLES:
        example_embedding = get_text_embedding(example)
        if example_embedding is not None:
            similarity = np.dot(user_embedding, example_embedding) / (np.linalg.norm(user_embedding) * np.linalg.norm(example_embedding))
            if similarity >= threshold:
                return True  # User message is likely a mentor request

    return False  # Default to False if no match
