import os
import openai
from flask import Flask, request, jsonify
import json
from flask_cors import CORS
from db_utils import load_students_data, save_students_data
import firebase_admin
from firebase_admin import credentials, firestore
from conversation_utils import (
    optimize_conversation_history,
    generate_messages,
    generate_conversation_starters,
    detect_goal_creation,
    parse_new_student_info
)
from competition_utils import (
    detect_science_project_request,
    detect_deca_request,
    generate_project_guidance,
    generate_deca_guidance
)
from mentor_utils import (
    should_recommend_mentor,
    recommend_mentor,
    generate_mentor_reason,
    is_explicit_mentor_request
)

# Load secrets securely from environment variables
openai.api_key = "ADD HERE"
SECRET_KEY = "ishaanjain"
mentors = ["Aalaap", "Anjan", "Vishnu", "Ishaan", "Shairee", "Shivani", "Rohan", "Annmaria"]

if not openai.api_key:
    raise ValueError("Missing OpenAI API Key. Set the OPENAI_API_KEY environment variable.")

# Flask app setup
app = Flask(__name__)
app.secret_key = SECRET_KEY
CORS(app, resources={r"/api/*": {"origins": ["https://open-admit-ai.vercel.app", "http://localhost:5000"]}},
     supports_credentials=True,
     methods=["GET", "POST", "OPTIONS"])

# In-memory user sessions
user_sessions = {}
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

def get_student_data(student_id):
    student_ref = db.collection("students").document(student_id)
    student = student_ref.get()
    return student.to_dict() if student.exists else None

def save_student_data(student_id, student_data):
    student_ref = db.collection("students").document(student_id)
    student_ref.set(student_data)

def update_student_data(student_id, update_fields):
    student_ref = db.collection("students").document(student_id)
    student_ref.update(update_fields)


def add_goal(student_id, new_goal):
    """Adds a new goal to the student's profile in Firestore."""
    student_ref = db.collection("students").document(student_id)
    student = student_ref.get()

    if student.exists:
        student_data = student.to_dict()
        goals = student_data.get("goals", [])

        # Prevent duplicate goals
        if new_goal not in goals:
            goals.append(new_goal)
            student_ref.update({"goals": goals})
            return True  # Goal was added
    return False  # Goal was not added

def get_student_goals(student_id):
    """Fetches the list of student goals from Firestore."""
    student_ref = db.collection("students").document(student_id)
    student = student_ref.get()

    if student.exists:
        return student.to_dict().get("goals", [])
    return []

@app.route('/api/goals/<student_id>', methods=['GET'])
def get_goals(student_id):
    student_id = student_id.strip().lower()
    student_info = get_student_data(student_id)
    if not student_info:
        return jsonify({"error": "Student not found"}), 404
    goals = student_info.get("goals", [])
    return jsonify({"goals": goals})

def extract_goals_from_text(text):
    """
    Uses a two-pass approach with GPT to extract or generate actionable goals
    from the given text. Returns a list of goal statements (strings).
    """
    # First pass: Attempt to extract explicit goal recommendations as a JSON array.
    prompt = (
        "Extract one actionable goal recommendation from the following text. "
        "Return the result as a JSON array of goal statements. If there are none, return an empty JSON array.\n\n"
        f"Text: {text}"
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an assistant that extracts actionable goals from text."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100,
            temperature=0.0
        )
        output = response.choices[0].message.content.strip()
        goals = json.loads(output)
        if isinstance(goals, list) and len(goals) > 0:
            return goals
    except Exception as e:
        print("Error in primary goal extraction:", e)

    # Fallback pass: Generate one or two actionable goals even if not explicitly stated.
    fallback_prompt = (
        "Based on the following advice or instructions, generate one actionable goal "
        "that a student can take. Return the result as a JSON array of goal statements. "
        "If no actionable goals can be generated, return an empty JSON array.\n\n"
        f"Text: {text}"
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an assistant that generates actionable student goals from advice."},
                {"role": "user", "content": fallback_prompt}
            ],
            max_tokens=150,
            temperature=0.0
        )
        output = response.choices[0].message.content.strip()
        print(output)
        goals = json.loads(output)
        if isinstance(goals, list):
            return goals
    except Exception as e:
        print("Error in fallback goal extraction:", e)
    return []

# Function to manage user sessions
def get_or_create_user_session(student_id):
    if student_id not in user_sessions:
        user_sessions[student_id] = {
            'conversation': [],
            'conversation_summary': "",
            'mentor_cooldown': 3,
            'science_project_stage': "none",
            'deca_stage': "none",
            'goal_cooldown': 0  # New field to prevent goals from being created too often
        }
    return user_sessions[student_id]

# Chat function with OpenAI
def _chat_with_athena(student_info, conversation, conversation_summary):
    try:
        messages_for_model = generate_messages(student_info, conversation, conversation_summary)
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=messages_for_model,
            max_tokens=300,
            temperature=0.8
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error in AI response: {str(e)}"

@app.route('/api/student', methods=['POST'])
def create_or_update_student():
    try:
        data = request.get_json()
        student_id = data.get('name', '').strip().lower()  # Normalize to lowercase

        if not student_id:
            return jsonify({"error": "Name is required"}), 400

        student_data = get_student_data(student_id)

        # If student does not exist, initialize a new record
        if not student_data:
            student_data = {
                'name': data.get('name', ''),
                'grade': data.get('grade', ''),
                'future_study': data.get('future_study', ''),
                'deep_interest': data.get('deep_interest', ''),
                'current_extracurriculars': data.get('current_extracurriculars', ''),
                'favorite_courses': data.get('favorite_courses', ''),
                'competitions': [],
                'notes': [],
                'goals': [],
                'conversation_summary': "",
                'last_conversation': []
            }
        else:
            # Update existing student details
            for key in ['grade', 'future_study', 'deep_interest',
                        'current_extracurriculars', 'favorite_courses']:
                if key in data:
                    student_data[key] = data[key]

        save_student_data(student_id, student_data)
        get_or_create_user_session(student_id)

        return jsonify({"student_id": student_id, "message": "Student created/updated successfully"})

    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

# Get conversation starters
@app.route('/api/starters/<student_id>', methods=['GET'])
def get_conversation_starters(student_id):
    try:
        student_id = student_id.strip().lower()
        student_info = get_student_data(student_id)

        if not student_info:
            return jsonify({"error": "Student not found"}), 404

        session_data = get_or_create_user_session(student_id)
        conversation = session_data['conversation']

        starters = generate_conversation_starters(student_info, conversation)
        return jsonify({"starters": starters})

    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/chat', methods=['OPTIONS'])
def handle_options():
    response = jsonify({"message": "CORS preflight request handled"})
    response.headers.add("Access-Control-Allow-Origin", "https://open-admit-ai.vercel.app")
    response.headers.add("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type, Authorization")
    response.headers.add("Access-Control-Allow-Credentials", "true")
    return response

@app.route('/api/summarize_mentor_convo', methods=['POST'])
def summarize_mentor_convo():
    data = request.get_json()
    message = data.get('messages', '').strip()
    try:
        summary_prompt = [
            {"role" : "system", "content" : "Summarize the following conversation session in a concise way, capturing key topics, advice given, and major takeaways."},
            {"role" : "user", "content" : message}
        ]

        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=summary_prompt,
            max_tokens=50,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error in generating summary: {str(e)}"


@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        student_id = data.get('student_id', '').strip().lower()
        user_message = data.get('message', '').strip()
        if not student_id:
            return jsonify({"error": "student_id is required"}), 400
        if not user_message:
            return jsonify({"error": "message is required"}), 400

        session_data = get_or_create_user_session(student_id)
        conversation = session_data['conversation']
        conversation_summary = session_data['conversation_summary']
        recommended_mentor_id = None

        # 1) Fetch student data from Firestore
        student_info = get_student_data(student_id)
        if not student_info:
            student_info = {
                'name': '',
                'grade': '',
                'future_study': '',
                'deep_interest': '',
                'current_extracurriculars': '',
                'favorite_courses': '',
                'competitions': [],
                'notes': [],
                'goals': [],
                'conversation_summary': "",
                'last_conversation': []
            }

        # 2) Append user's message and update summary if needed
        conversation.append({'role': 'user', 'content': user_message})
        conversation, conversation_summary = optimize_conversation_history(conversation, conversation_summary)
        print("Updated conversation summary:", conversation_summary)

        # 3) Extract new/updated info and disclaimers from GPT
        parsed_result = parse_new_student_info(user_message, student_info, conversation_summary)
        updates = parsed_result.get("updates", {})
        disclaimers = parsed_result.get("disclaimers", "")
        if updates:
            for key, value in updates.items():
                if isinstance(student_info.get(key), list) and isinstance(value, list):
                    existing_set = set(student_info.get(key, []))
                    existing_set.update(value)
                    student_info[key] = list(existing_set)
                else:
                    student_info[key] = value
        if disclaimers:
            if not isinstance(student_info.get('notes'), list):
                student_info['notes'] = []
            student_info['notes'].append(f"DISCLAIMER: {disclaimers}")
        if updates or disclaimers:
            save_student_data(student_id, student_info)

        # 4) Handle mentor request logic
        if is_explicit_mentor_request(user_message):
            best_mentor, best_score = recommend_mentor(user_message, student_info)
            if best_mentor:
                recommended_mentor_id = mentors.index(best_mentor)
                reason = generate_mentor_reason(best_mentor, user_message)
                conversation.append({
                    'role': 'assistant',
                    'content': f"Mentor Recommendation: **{best_mentor}** (Score: {best_score:.2f})\n\n{reason}"
                })
                session_data['mentor_cooldown'] = 3
        else:
            # Handle competition requests...
            if session_data['science_project_stage'] == "clarifying":
                session_data['science_project_stage'] = "generate"
                project_idea = generate_project_guidance(student_info, conversation)
                conversation.append({'role': 'assistant', 'content': project_idea})
                session_data['science_project_stage'] = "none"
            elif session_data['deca_stage'] == "clarifying":
                session_data['deca_stage'] = "generate"
                deca_advice = generate_deca_guidance(student_info, conversation)
                conversation.append({'role': 'assistant', 'content': deca_advice})
                session_data['deca_stage'] = "none"
            elif detect_science_project_request(user_message):
                session_data['science_project_stage'] = "clarifying"
                conversation.append({'role': 'assistant', 'content': "What are your science interests? Lab work, data analysis, or something else?"})
            elif detect_deca_request(user_message):
                session_data['deca_stage'] = "clarifying"
                conversation.append({'role': 'assistant', 'content': "Which DECA category are you interested in? Finance, hospitality, marketing...?"})
            else:
                # Fallback: Generate assistant's response normally.
                assistant_message = _chat_with_athena(student_info, conversation, conversation_summary)
                conversation.append({'role': 'assistant', 'content': assistant_message})

                # 5) Goal Extraction: Only run if goal cooldown is 0.
                if session_data.get('goal_cooldown', 0) == 0:
                    simple_goal = detect_goal_creation(assistant_message)
                    nlp_goals = extract_goals_from_text(assistant_message)
                    all_goals = set()
                    if simple_goal:
                        all_goals.add(simple_goal)
                    for goal in nlp_goals:
                        if goal.strip():
                            all_goals.add(goal.strip())
                    # Check for redundancy with current goals
                    existing_goals = set(student_info.get("goals", []))
                    new_goals = [goal for goal in all_goals if goal not in existing_goals]
                    for goal in new_goals:
                        if add_goal(student_id, goal):
                            conversation.append({
                                'role': 'assistant',
                                'content': f"✅ I’ve officially added **'{goal}'** to your goals in the dashboard!"
                            })
                    # Set a cooldown (e.g., 3 turns) after adding a goal
                    if new_goals:
                        session_data['goal_cooldown'] = 5
                else:
                    # Decrement the goal cooldown counter if it's active.
                    session_data['goal_cooldown'] -= 1

                # 6) Passive mentor recommendation based on cooldown
                if session_data['mentor_cooldown'] > 0:
                    session_data['mentor_cooldown'] -= 1
                else:
                    if should_recommend_mentor(user_message):
                        best_mentor, best_score = recommend_mentor(user_message, student_info)
                        if best_mentor:
                            recommended_mentor_id = mentors.index(best_mentor)
                            reason = generate_mentor_reason(best_mentor, user_message)
                            conversation.append({
                                'role': 'assistant',
                                'content': f"Mentor Recommendation: **{best_mentor}** (Score: {best_score:.2f})\n\n{reason}"
                            })
                            session_data['mentor_cooldown'] = 3

        # 7) Update session data and save updated conversation logs to Firestore
        session_data['conversation'] = conversation
        session_data['conversation_summary'] = conversation_summary
        update_student_data(student_id, {
            "last_conversation": conversation,
            "conversation_summary": conversation_summary
        })

        return jsonify({
            "conversation": conversation,
            "last_response": conversation[-1]['content'],
            "mentor_id": recommended_mentor_id
        })

    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

# Run Flask app
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
