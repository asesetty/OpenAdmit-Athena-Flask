import os
import openai
from flask import Flask, request, jsonify
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

app = Flask(__name__)


openai.api_key = "ADD HERE"
app.secret_key = "athena123"

user_sessions = {}


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


def _chat_with_athena(student_info, conversation, conversation_summary):
    messages_for_model = generate_messages(student_info, conversation, conversation_summary)
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=messages_for_model,
        max_tokens=300,
        temperature=0.8
    )
    return response.choices[0].message.content.strip()


# Student Creation/Update Endpoint
@app.route('/api/student', methods=['POST'])
def create_or_update_student():
    data = request.get_json()

    student_id = data.get('name', '').strip()  # Or some other logic for unique ID
    if not student_id:
        return jsonify({"error": "Name is required"}), 400

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

            # Additional fields for ongoing data
            'competitions': [],
            'notes': []
        }
    else:
        for key in [
            'grade', 'future_study', 'deep_interest', 'unique_something',
            'current_extracurriculars', 'favorite_courses'
        ]:
            if key in data:
                students_data[student_id][key] = data[key]

    save_students_data(students_data)

    get_or_create_user_session(student_id)

    return jsonify({
        "student_id": student_id,
        "message": "Student created/updated successfully"
    })


# Conversation Starters Endpoint
@app.route('/api/starters/<student_id>', methods=['GET'])
def get_conversation_starters(student_id):
    """
    Returns an array of suggested conversation starters based on the student's info.
    """
    students_data = load_students_data()

    if student_id not in students_data:
        return jsonify({"error": "Student not found"}), 404

    student_info = students_data[student_id]
    # We can pass an empty list or the existing conversation from the user session
    session_data = get_or_create_user_session(student_id)
    conversation = session_data['conversation']

    starters = generate_conversation_starters(student_info, conversation)
    return jsonify({"starters": starters})


# Chat/Conversation Endpoint
@app.route('/api/chat', methods=['POST'])
def chat():
    """
    Expects JSON with:
      {
        "student_id": "...",
        "message": "User's message"
      }
    Returns the system's updated conversation or the AI's response in JSON form.
    """
    data = request.get_json()
    student_id = data.get('student_id', '').strip()
    user_message = data.get('message', '').strip()

    if not student_id:
        return jsonify({"error": "student_id is required"}), 400
    if not user_message:
        return jsonify({"error": "message is required"}), 400

    # Load or create user session data
    session_data = get_or_create_user_session(student_id)
    conversation = session_data['conversation']
    conversation_summary = session_data['conversation_summary']

    # Retrieve or confirm we have the student's info
    students_data = load_students_data()
    if student_id not in students_data:
        return jsonify({"error": "Student not found"}), 404

    student_info = students_data[student_id]

    # Append user's message
    conversation.append({'role': 'user', 'content': user_message})

    # Possibly optimize conversation
    conversation, conversation_summary = optimize_conversation_history(conversation, conversation_summary)

    # Check for special competition requests
    is_science_request = detect_science_project_request(user_message)
    is_deca_request = detect_deca_request(user_message)

    # If in the clarifying stage for science project
    if session_data['science_project_stage'] == "clarifying":
        session_data['science_project_stage'] = "generate"
        conversation.append({
            'role': 'assistant',
            'content': "Got it. Let me think through a solid project idea for you..."
        })
        project_idea = generate_project_guidance(student_info, conversation)
        conversation.append({'role': 'assistant', 'content': project_idea})
        session_data['science_project_stage'] = "none"

    # If in the clarifying stage for DECA
    elif session_data['deca_stage'] == "clarifying":
        session_data['deca_stage'] = "generate"
        conversation.append({
            'role': 'assistant',
            'content': "Great, thanks for the details. Here's some DECA-specific guidance..."
        })
        deca_advice = generate_deca_guidance(student_info, conversation)
        conversation.append({'role': 'assistant', 'content': deca_advice})
        session_data['deca_stage'] = "none"

    # If new science project request triggers
    elif is_science_request:
        session_data['science_project_stage'] = "clarifying"
        clarifying_msg = (
            "I'd love to help you with a science project! "
            "Could you tell me more about your interests? For example, do you prefer lab work, data analysis, or something else?"
        )
        conversation.append({'role': 'assistant', 'content': clarifying_msg})

    # If new DECA request triggers
    elif is_deca_request:
        session_data['deca_stage'] = "clarifying"
        clarifying_msg = (
            "Sure! DECA is great for developing business and marketing skills. "
            "Which events or areas do you have in mind? Finance, hospitality, marketing...?"
        )
        conversation.append({'role': 'assistant', 'content': clarifying_msg})

    # Otherwise, normal AI response
    else:
        assistant_message = _chat_with_athena(student_info, conversation, conversation_summary)
        conversation.append({'role': 'assistant', 'content': assistant_message})

        # Mentor recommendation logic
        if session_data['mentor_cooldown'] > 0:
            session_data['mentor_cooldown'] -= 1
        else:
            if should_recommend_mentor(user_message):
                best_mentor, best_score = recommend_mentor(user_message, student_info)
                if best_mentor:
                    reason = generate_mentor_reason(best_mentor, user_message)
                    recommendation_text = (
                        f"I think you might benefit from chatting with Mentor **{best_mentor}** "
                        f"(similarity score: {best_score:.2f}).\n\n"
                        f"I recommended them because {reason}\n\n"
                        f"[Click here to chat with {best_mentor}]"
                    )
                    conversation.append({'role': 'assistant', 'content': recommendation_text})
                    session_data['mentor_cooldown'] = 5

    # Update conversation summary in session
    session_data['conversation'] = conversation
    session_data['conversation_summary'] = conversation_summary

    # Return entire conversation or just the last response
    return jsonify({
        "conversation": conversation,
        "last_response": conversation[-1]['content']
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
