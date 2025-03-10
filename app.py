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
from datetime import datetime  # For timestamp in topics summaries

# Load secrets securely from environment variables
openai.api_key = "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"
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


# Firebase functions
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
        if new_goal not in goals:
            goals.append(new_goal)
            student_ref.update({"goals": goals})
            return True
    return False

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
        output = output[8:]
        output = output[ :len(output) - 3]
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
            'goal_cooldown': 0,
            'research_state': "none"  # Tracks the research workflow state.
        }
    return user_sessions[student_id]

# Chat function with OpenAI (fallback for general conversation)
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


# --- Research Workflow Templates & GPT-based Helpers ---
RESEARCH_TEMPLATES = {
    "step1": "Based on your profile, here are some potential research fields that may interest you: {fields}. Do any of these spark your interest?",
    "step2": (
        "Great! Here are a few common research paths:\n"
        "1. Working with a Professor – assisting on existing projects.\n"
        "2. Enrolling in a Research Program – structured research experiences.\n"
        "3. Independent Research with a University Student – flexible mentorship-based projects.\n"
        "Do any of these paths interest you?"
    ),
    "step3": (
        "Before we get started, here’s what past mentors have done in similar situations:\n"
        "Example: At your age, Ishaan leveraged his background in A, B, and C to achieve X.\n"
        "Would you like to connect with a mentor for more details or jump straight into your research journey?"
    ),
    "step4": (
        "Let's dive into the specifics of your chosen research path. Here’s a brief overview and the next steps:\n"
        "{path_details}\n"
        "Please follow the outlined tasks and update me when you're ready to move forward."
    ),
    "final": "Great! Your research journey has been set up. Let me know if you have any further questions or need additional guidance."
}

def generate_research_fields(student_info):
    """
    Uses GPT to generate a comma-separated list of research fields based on the student's deep interests and favorite courses.
    """
    prompt = (
        f"Based on the student's information below, generate a comma-separated list of 3 to 5 potential research fields that align with their interests.\n"
        f"Deep Interests: {student_info.get('deep_interest', 'Not specified')}\n"
        f"Favorite Courses: {student_info.get('favorite_courses', 'Not specified')}\n"
        "Output:"
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an academic advisor."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=50,
            temperature=0.3
        )
        fields = response.choices[0].message.content.strip()
        return fields
    except Exception as e:
        print("Error generating research fields:", e)
        return "Interdisciplinary Science, Emerging Technologies, Environmental Studies"

def generate_path_details(student_info):
    """
    Uses GPT to generate a concise overview of next steps for pursuing a research opportunity,
    based on the student's future study interests, grade, and deep interests.
    """
    prompt = (
        f"Based on the student's information below, generate a concise overview of actionable next steps for pursuing a research opportunity.\n"
        f"Future Study Interests: {student_info.get('future_study', 'Not specified')}\n"
        f"Grade: {student_info.get('grade', 'Not specified')}\n"
        f"Deep Interests: {student_info.get('deep_interest', 'Not specified')}\n"
        "Include suggestions like contacting professors, drafting personalized emails, or researching programs.\n"
        "Output:"
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an academic advisor."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100,
            temperature=0.3
        )
        details = response.choices[0].message.content.strip()
        return details
    except Exception as e:
        print("Error generating path details:", e)
        return "Identify relevant labs and programs, draft personalized outreach emails, and follow up within a week."

def process_research_workflow(student_info, session_data, user_message):
    """
    Processes a sequential and personalized research workflow using GPT-driven responses and pre-defined templates.
    Uses a finite-state machine to determine the next step.
    """
    state = session_data.get("research_state", "none")
    print("RESEARCH", state)
    if state == "none" and "research" in user_message.lower() and "get started" in user_message.lower():
        fields = generate_research_fields(student_info)
        session_data["research_state"] = "step1"
        return RESEARCH_TEMPLATES["step1"].format(fields=fields)
    elif state == "step1":
        if "yes" in user_message.lower():
            session_data["research_state"] = "step2"
            return RESEARCH_TEMPLATES["step2"]
        else:
            fields = generate_research_fields(student_info)
            return "Let's try again. " + RESEARCH_TEMPLATES["step1"].format(fields=fields)
    elif state == "step2":
        session_data["research_state"] = "step3"
        return RESEARCH_TEMPLATES["step3"]
    elif state == "step3":
        if "mentor" in user_message.lower():
            session_data["research_state"] = "none"
            return "I'll connect you with a mentor to discuss your research options further."
        elif "jump" in user_message.lower():
            session_data["research_state"] = "step4"
            path_details = generate_path_details(student_info)
            return RESEARCH_TEMPLATES["step4"].format(path_details=path_details)
        else:
            return "Please indicate if you'd like to speak to a mentor or jump straight into your research journey."
    elif state == "step4":
        session_data["research_state"] = "none"
        return RESEARCH_TEMPLATES["final"]
    return None

# --- Summaries with Timestamp for Topics ---
from datetime import datetime

def shorten_topic_sentence(topic, chat_partner):
    """
    Uses a low-token GPT call to produce a concise summary sentence for a topic,
    indicating that the student talked with the specified chat partner.
    We also add a short timestamp so it appears user-friendly on the "recent conversations" UI.
    """
    # Produce a short summary via GPT
    prompt = (
        f"Summarize the following topic in one short, concise sentence that indicates the student talked with {chat_partner}: {topic}. Write it in first person view from the student's side (ex. if a student chats with Athena, it would say something like 'Talked to Athena about <...>'. It does not have to follow that EXACT structure, but keep the point of view like that."
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a concise text summarizer."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=20,
            temperature=0.2
        )
        summary = response.choices[0].message.content.strip()
    except Exception as e:
        print("Error shortening topic sentence:", e)
        summary = f"Discussed '{topic}' with {chat_partner}."

    # Add a short timestamp prefix
    #dt_str = datetime.now().strftime("%b %d, %Y • %I:%M %p")
    return f"{summary}"

def update_student_topics(student_id, new_topic_sentence):
    """
    Updates the student's topics array by prepending the new topic.
    Keeps only the most recent 50 topics and returns the updated topics list.
    """
    student_ref = db.collection("students").document(student_id)
    student = student_ref.get()
    if student.exists:
        student_data = student.to_dict()
        topics = student_data.get("topics", [])
        topics.insert(0, new_topic_sentence)
        topics = topics[:50]
        student_ref.update({"topics": topics})
        return topics
    return None
# --- End Summaries with Timestamp ---

@app.route('/api/student', methods=['POST'])
def create_or_update_student():
    try:
        data = request.get_json()
        student_id = data.get('name', '').strip().lower()
        if not student_id:
            return jsonify({"error": "Name is required"}), 400

        student_data = get_student_data(student_id)
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
                'last_conversation': [],
                'topics': []
            }
        else:
            for key in ['grade', 'future_study', 'deep_interest', 'current_extracurriculars', 'favorite_courses']:
                if key in data:
                    student_data[key] = data[key]

        save_student_data(student_id, student_data)
        get_or_create_user_session(student_id)
        return jsonify({"student_id": student_id, "message": "Student created/updated successfully"})

    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

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
            {"role": "system",
             "content": "Summarize the following conversation session in a concise way, capturing key topics, advice given, and major takeaways."},
            {"role": "user", "content": message}
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
                'last_conversation': [],
                'topics': []
            }

        # --- Research Workflow Check ---
        research_response = process_research_workflow(student_info, session_data, user_message)
        if research_response is not None:
            conversation.append({'role': 'assistant', 'content': research_response})
            session_data['conversation'] = conversation
            session_data['conversation_summary'] = conversation_summary
            update_student_data(student_id, {
                "last_conversation": conversation,
                "conversation_summary": conversation_summary
            })
            return jsonify({
                "conversation": conversation,
                "last_response": research_response,
                "mentor_id": None
            })
        # --- End Research Workflow Check ---

        # 2) Append user's message and update summary if needed
        conversation.append({'role': 'user', 'content': user_message})
        conversation, conversation_summary = optimize_conversation_history(conversation, conversation_summary)
        print("Updated conversation summary:", conversation_summary)

        # --- Live Topics Update Integration ---
        # Generate a short summary of the user's message using a low-token GPT call + timestamp
        short_topic = shorten_topic_sentence(user_message, "Athena")
        updated_topics = update_student_topics(student_id, short_topic)
        if updated_topics is not None:
            student_info["topics"] = updated_topics
        # --- End Topics Update ---

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
            # Use a circular buffer for disclaimers: only add if not duplicate and keep only latest 10
            disclaimer_text = f"DISCLAIMER: {disclaimers}"
            if disclaimer_text not in student_info['notes']:
                student_info['notes'].append(disclaimer_text)
                if len(student_info['notes']) > 10:
                    student_info['notes'] = student_info['notes'][-10:]
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
            # Handle competition requests or fallback to normal conversation
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
                    existing_goals = set(student_info.get("goals", []))
                    new_goals = [goal for goal in all_goals if goal not in existing_goals]
                    for goal in new_goals:
                        if add_goal(student_id, goal):
                            conversation.append({
                                'role': 'assistant',
                                'content': f"✅ I’ve officially added **'{goal}'** to your goals in the dashboard!"
                            })
                    if new_goals:
                        session_data['goal_cooldown'] = 5
                else:
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

@app.route('/api/student_bio/<student_id>', methods=['GET'])
def generate_student_bio(student_id):
    student_id = student_id.strip().lower()
    student_info = get_student_data(student_id)
    if not student_info:
        return jsonify({"error": "Student not found"}), 404

    structured_info = {
        "name": student_info.get("name", "Unknown"),
        "grade": student_info.get("grade", "Not specified"),
        "future_study": student_info.get("future_study", "Not specified"),
        "deep_interest": student_info.get("deep_interest", "Not specified"),
        "current_extracurriculars": student_info.get("current_extracurriculars", "None"),
        "favorite_courses": student_info.get("favorite_courses", "None"),
        "competitions": student_info.get("competitions", []),
        "goals": student_info.get("goals", []),
    }

    bio_prompt = (
        f"Create a concise and well-structured 3-4 sentence biography for a student "
        f"based on the following details:\n\n"
        f"Name: {structured_info['name']}\n"
        f"Grade: {structured_info['grade']}\n"
        f"Future Study Interests: {structured_info['future_study']}\n"
        f"Deep Interests: {structured_info['deep_interest']}\n"
        f"Current Extracurriculars: {structured_info['current_extracurriculars']}\n"
        f"Favorite Courses: {structured_info['favorite_courses']}\n"
        f"Competitions: {', '.join(structured_info['competitions']) if structured_info['competitions'] else 'None'}\n"
        f"Goals: {', '.join(structured_info['goals']) if structured_info['goals'] else 'None'}\n\n"
        f"Ensure the summary is natural, engaging, and informative without cutting off."
    )

    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an AI assistant that creates concise and engaging student bios."},
                {"role": "user", "content": bio_prompt}
            ],
            max_tokens=200,
            temperature=0.7,
        )
        student_bio = response.choices[0].message.content.strip()
        return jsonify({"bio": student_bio})
    except Exception as e:
        print(f"Error generating student bio: {e}")
        return jsonify({"error": "Failed to generate student bio"}), 500

@app.route('/api/topics/<student_id>', methods=['GET'])
def get_topics(student_id):
    """
    Fetches the student's topics array from Firestore, which contains
    short GPT-generated summaries of recent conversation entries.
    """
    student_id = student_id.strip().lower()
    student_info = get_student_data(student_id)
    if not student_info:
        return jsonify({"error": "Student not found"}), 404

    topics = student_info.get("topics", [])
    return jsonify({"topics": topics})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
