import os
import openai
from flask import Flask, request, jsonify
from flask_cors import CORS
from db_utils import load_students_data, save_students_data
from conversation_utils import (
    optimize_conversation_history,
    generate_messages,
    generate_conversation_starters
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
    generate_mentor_reason
)

# Load secrets securely from environment variables
openai.api_key = "Add here"
SECRET_KEY = "ishaanjain"
mentors = ["Aalaap", "Anjan", "Vishnu", "Ishaan", "Shairee", "Shivani", "Rohan", "Annmaria"]

if not openai.api_key:
    raise ValueError("Missing OpenAI API Key. Set the OPENAI_API_KEY environment variable.")

# Flask app setup
app = Flask(__name__)
app.secret_key = SECRET_KEY
CORS(app, resources={r"/api/*": {"origins": ["https://open-admit-ai.vercel.app", "http://localhost:3000", "http://localhost:3001"]}},
     supports_credentials=True,
     methods=["GET", "POST", "OPTIONS"])

# In-memory user sessions
user_sessions = {}

# Function to manage user sessions
def get_or_create_user_session(student_id):
    if student_id not in user_sessions:
        user_sessions[student_id] = {
            'conversation': [],
            'conversation_summary': "",
            'mentor_cooldown': 0,
            'science_project_stage': "none",
            'deca_stage': "none"
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

# Create or update student
@app.route('/api/student', methods=['POST'])
def create_or_update_student():
    try:
        data = request.get_json()
        student_id = data.get('name', '').strip().lower()  # Normalize to lowercase

        if not student_id:
            return jsonify({"error": "Name is required"}), 400

        students_data = load_students_data()

        # Initialize student if not exists
        if student_id not in students_data:
            students_data[student_id] = {
                'name': data.get('name', ''),
                'grade': data.get('grade', ''),
                'future_study': data.get('future_study', ''),
                'deep_interest': data.get('deep_interest', ''),
                'unique_something': data.get('unique_something', ''),
                'current_extracurriculars': data.get('current_extracurriculars', ''),
                'favorite_courses': data.get('favorite_courses', ''),
                'competitions': [],
                'notes': []
            }
        else:
            # Update existing student details
            for key in ['grade', 'future_study', 'deep_interest', 'unique_something',
                        'current_extracurriculars', 'favorite_courses']:
                if key in data:
                    students_data[student_id][key] = data[key]

        save_students_data(students_data)
        get_or_create_user_session(student_id)

        return jsonify({"student_id": student_id, "message": "Student created/updated successfully"})

    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

# Get conversation starters
@app.route('/api/starters/<student_id>', methods=['GET'])
def get_conversation_starters(student_id):
    try:
        student_id = student_id.strip().lower()
        students_data = load_students_data()

        if student_id not in students_data:
            return jsonify({"error": "Student not found"}), 404

        student_info = students_data[student_id]
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

        students_data = load_students_data()
        if student_id not in students_data:
            students_data[student_id] = {
                'name': data.get('name', ''),
                'grade': data.get('grade', ''),
                'future_study': data.get('future_study', ''),
                'deep_interest': data.get('deep_interest', ''),
                'unique_something': data.get('unique_something', ''),
                'current_extracurriculars': data.get('current_extracurriculars', ''),
                'favorite_courses': data.get('favorite_courses', ''),
                'competitions': [],
                'notes': []
            }

        student_info = students_data[student_id]

        # Append user's message
        conversation.append({'role': 'user', 'content': user_message})
        conversation, conversation_summary = optimize_conversation_history(conversation, conversation_summary)

        # Handle competition requests
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
            assistant_message = _chat_with_athena(student_info, conversation, conversation_summary)
            conversation.append({'role': 'assistant', 'content': assistant_message})

            # Initialize recommended_mentor_id to None for this chat response.
            recommended_mentor_id = None

            # Mentor recommendation logic
            if session_data['mentor_cooldown'] > 0:
                session_data['mentor_cooldown'] -= 1
            else:
                if should_recommend_mentor(user_message):
                    best_mentor, best_score = recommend_mentor(user_message, student_info)
                    if best_mentor:
                        recommended_mentor_id = mentors.index(best_mentor)  # Capture the mentor ID.
                        reason = generate_mentor_reason(best_mentor, user_message)
                        conversation.append({'role': 'assistant', 'content': f"Mentor Recommendation: **{best_mentor}** (Score: {best_score:.2f})\n\n{reason}"})
                        session_data['mentor_cooldown'] = 5

        session_data['conversation'] = conversation
        session_data['conversation_summary'] = conversation_summary

        # Return the conversation along with the mentor_id (if a mentor was recommended)
        return jsonify({
            "conversation": conversation,
            "last_response": conversation[-1]['content'],
            "mentor_id": recommended_mentor_id
        })

    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

# Run Flask app
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000, debug=True)
