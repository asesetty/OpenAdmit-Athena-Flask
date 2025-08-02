import os
import openai
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# Import helper functions from your modules
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

# -------------------------------
# CONFIGURATION & INITIALIZATION
# -------------------------------
openai.api_key = "ADD HERE"
SECRET_KEY = "test"

app = Flask(__name__)
app.secret_key = SECRET_KEY
CORS(app, resources={r"/api/*": {"origins": ["https://open-admit-ai.vercel.app", "http://localhost:5000", "http://localhost:3000", "http://localhost:3001", "http://localhost:5001"]}},
     supports_credentials=True,
     methods=["GET", "POST", "OPTIONS"])

# Initialize Firebase Admin with your service account key file
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# -------------------------------
# WORKFLOW TEMPLATES (Dynamic Prompt Bases)
# -------------------------------
# Research Workflow (Highest priority)
RESEARCH_WORKFLOW = {
    "step1_intro": {
        "prompt": ("I want to get started in research. Based on your profile, here are some potential fields that may interest you: [X, Y, Z]. "
                   "Do any of these topics interest you? (Yes/No)")
    },
    "step2_types": {
        "prompt": ("Before we start, here are a few general research paths: Working with a Professor, Enrolling in a Research Program, "
                   "or Independent Research with a University Student. Do any of these paths interest you? (Yes/No)")
    },
    "step3_mentor": {
        "prompt": ("Here’s what past mentors did in similar situations: [Mentor stories]. Did any of these projects excite you? "
                   "Would you like to speak to a mentor for more details, or would you like to jump straight into your research journey? (Mentor/Jump)")
    },
    "step4_details": {
        "prompt": ("Based on your choice, here is more detail on the specific research pathway. Let's start by drafting an outreach plan or an application checklist.")
    }
}

# DECA Workflow
DECA_WORKFLOW = {
    "step1_join": {
        "prompt": "I want to join DECA. Do you have a DECA chapter at your school? (Yes/No)",
        "response_if_yes": "Great! Since you have a DECA chapter at your school, please contact your DECA advisor or attend a meeting to officially join.",
        "response_if_no": "No worries! You can start a chapter at your school or join an independent DECA chapter. Would you like guidance on how to start one? (Yes/No)"
    },
    "step2_event_types": {
        "prompt": ("DECA offers multiple event categories including Role-Play & Case Study, Prepared, and Online Simulation events. "
                   "Would you like a detailed explanation of each event type? (Yes/No)"),
        "response_if_yes": "Providing detailed descriptions from DECA’s official guide, please hold on.",
        "response_if_no": "Proceeding to event selection. Which event type interests you? (Roleplay/Prepared/Online)"
    },
    "step3_roleplay": {
        "prompt": "Great! Role-Play/Case Study events involve a structured exam and case study. Do you prefer an individual event, a team decision-making event, or a personal financial literacy event?"
    },
    "step3_prepared": {
        "prompt": "Awesome! Prepared events involve a detailed project and presentation. What aspect interests you most? (e.g., Event Planning, Business Research, Entrepreneurship)"
    },
    "step3_online": {
        "prompt": "Online simulation events test your business strategy. Which challenge interests you? (e.g., Stock Market, Personal Finance, Restaurant, Retail, Sports)"
    }
}

# MUN Workflow
MUN_WORKFLOW = {
    "step1_join": {
        "prompt": "I want to join MUN. Do you have an MUN club at your school? (Yes/No)",
        "response_if_yes": "Great! Since you have an MUN club, please contact your MUN advisor or attend the club meeting to begin training.",
        "response_if_no": "No worries! You can start an MUN club or find external conferences. Would you like guidance on how to start one or locate conferences? (Yes/No)"
    },
    "step2_committees": {
        "prompt": ("MUN conferences include committees like General Assemblies, Crisis Committees, Specialized Agencies, and Regional Bodies. "
                   "Would you like a detailed explanation of each type? (Yes/No)"),
        "response_if_yes": "Providing detailed committee descriptions based on MUN guidelines.",
        "response_if_no": "Alright. Which committee interests you the most?"
    },
    "step3_research": {
        "prompt": "Let's move on to preparation. Would you like help with position paper writing, speech writing, or resolution writing? (Please specify)"
    },
    "step4_parliamentary": {
        "prompt": "Parliamentary procedure structures the debate. Would you like a cheat sheet on the rules? (Yes/No)"
    },
    "step5_registration": {
        "prompt": "You're ready to compete! Have you registered for the conference? (Yes/No)"
    }
}

# Podcast Creation Workflow
PODCAST_WORKFLOW = {
    "step1_concept": {
        "prompt": ("So you’re thinking about starting a podcast! What’s the main theme or purpose? "
                   "Are you sharing personal stories, interviewing guests, discussing a hobby, or covering school news? "
                   "Do you have a working concept? (Yes/No)"),
        "response_if_yes": "Great! Now let's move on to choosing your podcast format."
    },
    "step2_format": {
        "prompt": ("Now that you have a concept, which format appeals to you? Options include Solo Commentary, Co-Hosted, "
                   "Interview-Based, Narrative/Storytelling, or Hybrid. Please specify your choice or say 'not sure' for guidance."),
        "response_if_yes": "Excellent choice! Let's talk about equipment and software."
    },
    "step3_equipment": {
        "prompt": ("Let's be practical: what gear do you have? For example, do you have a USB microphone, headphones, "
                   "and recording software? If you're not sure, I can suggest budget-friendly options.")
    },
    "step4_branding": {
        "prompt": "Let's work on your podcast branding. What do you want to call your podcast and what vibe are you aiming for?"
    },
    "step5_episode_planning": {
        "prompt": ("Now let's plan your episodes. Have you thought of potential topics, an episode structure, and a release schedule? (Yes/No)")
    },
    "step6_recording": {
        "prompt": ("It's time to record your first episode! Do you need tips on script preparation, recording techniques, or editing? (Please specify)")
    },
    "step7_hosting": {
        "prompt": ("Where will you host your podcast? Options include Anchor, Buzzsprout, or Podbean. Have you decided on a platform? (Yes/No)")
    },
    "step8_marketing": {
        "prompt": ("Now that your podcast is live, how do you plan to get listeners? Would you like advice on social media promotion, "
                   "word-of-mouth strategies, or collaborations? (Please specify)")
    },
    "step9_improvement": {
        "prompt": ("Finally, how will you sustain and improve your podcast? Would you like strategies for collecting feedback, "
                   "adjusting formats, or exploring monetization options? (Yes/No)")
    }
}

# Science Olympiad Workflow
SCI_OLY_WORKFLOW = {
    "step1_categories": {
        "prompt": ("I want to compete in Science Olympiad but don’t know which event to choose. "
                   "Events are divided into three categories: Study Events (e.g., Anatomy & Physiology, Astronomy, Disease Detectives), "
                   "Lab-Based Events (e.g., Chem Lab, Experimental Design, Forensics), and "
                   "Build Events (e.g., Bridge, Flight, Scrambler). Do you want a detailed explanation of each event type? (Yes/No)")
    },
    "step2_select_event": {
        "prompt": ("How do I choose the best Science Olympiad event for me? Your choice should align with your interests, skills, "
                   "and team needs. Would you like a personalized recommendation based on your strengths? (Yes/No)")
    },
    "step3_preparation": {
        "prompt": ("How do I prepare for my Science Olympiad event? Preparation varies by event type. "
                   "For Study Events, gather official rules, create study guides, and take practice tests. "
                   "For Lab-Based Events, review lab techniques and practice experiments. "
                   "For Build Events, study the rules, prototype, and test your device. "
                   "Would you like sample tests, lab guides, or design tips? (Yes/No)")
    },
    "step4_strategies": {
        "prompt": ("What strategies can I use to perform well in Science Olympiad competitions? "
                   "General strategies include knowing the rules, time management, organization, and practicing under pressure. "
                   "Would you like event-specific strategies or past competition insights? (Yes/No)")
    },
    "step5_competition_day": {
        "prompt": ("I'm ready for my Science Olympiad competition. Here’s a checklist for competition day: "
                   "Bring required materials, arrive early, check your equipment, stay calm, and review your work. "
                   "Do you need further details or tips? (Yes/No)")
    }
}

# Volunteering & Nonprofit Workflow
VOLUNTEERING_WORKFLOW = {
    "step1_interests": {
        "prompt": ("Hey there! So you’re interested in volunteering, right? Can you think of any issue or area that sparks your passion? "
                   "Maybe tutoring, helping animal shelters, organizing clean-ups, etc.? If you’re unsure, please share a few ideas or say you have none.")
    },
    "step2_types": {
        "prompt": ("Now that you've identified a cause, how do you want to get involved? "
                   "Options include joining an existing organization, one-time events, starting a local initiative, or launching an official nonprofit. "
                   "Which path interests you? (existing, one-time, local, nonprofit)")
    },
    "step3_examples": {
        "prompt": ("Before we jump into tasks, let me share some stories from high school students who volunteered in areas like education, wildlife, or mental health. "
                   "Did any of these stories spark ideas for you? (Yes/No)")
    },
    "step4_existing": {
        "prompt": ("If you decided to join an existing organization or do one-time events, make a list of 3-5 organizations or events aligned with your cause. "
                   "Do you need help finding them? (Yes/No)")
    },
    "step5_local_initiative": {
        "prompt": ("If you prefer starting a local initiative, think about a need in your community (e.g., tutoring or a reading club). "
                   "Are you ready to pilot a local project? (Yes/No)")
    },
    "step6_nonprofit": {
        "prompt": ("If you're serious about starting an official nonprofit, you'll need to define your mission, research legal steps, form a board, and set up operations. "
                   "Would you like guidance on this process? (Yes/No)")
    },
    "step7_considerations": {
        "prompt": ("Lastly, consider awards, virtual volunteering, and collaborations with school clubs as ways to boost your profile. "
                   "Do these options interest you? (Yes/No)")
    }
}

# -------------------------------
# DETECT FUNCTIONS
# -------------------------------
def generate_workflow_response(prompt_template, student_info=None, user_message=None):
    prompt = prompt_template
    if student_info:
        student_context = f"\nStudent Info: {json.dumps(student_info)}"
        prompt += student_context
    if user_message:
        prompt += f"\nUser said: {user_message}"
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Generate structured, context-aware responses for workflow steps."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error generating workflow response: {str(e)}"

# -------------------------------
# GPT-BASED CLASSIFICATION FUNCTIONS
# -------------------------------
def classify_deca_input(current_step, user_message):
    prompt = (
        f"Current DECA step: {current_step}\nUser message: '{user_message}'\n"
        "Return a JSON with key 'answer' (yes or no) or key 'event_type' (roleplay, prepared, online)."
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Classify user input for the DECA workflow."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=50,
            temperature=0.0
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        print("Error in classify_deca_input:", e)
        return {}

def classify_mun_input(current_step, user_message):
    prompt = (
        f"Current MUN step: {current_step}\nUser message: '{user_message}'\n"
        "Return a JSON with key 'answer' (yes or no) or key 'committee' (General Assemblies, Crisis Committees, Specialized Agencies, Regional Bodies)."
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Classify user input for the MUN workflow."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=50,
            temperature=0.0
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        print("Error in classify_mun_input:", e)
        return {}

def classify_podcast_input(current_step, user_message):
    prompt = (
        f"Current Podcast step: {current_step}\nUser message: '{user_message}'\n"
        "Return a JSON with key 'answer' (yes or no) or key 'choice' (solo, co-hosted, interview, narrative, hybrid)."
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Classify user input for the Podcast workflow."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=50,
            temperature=0.0
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        print("Error in classify_podcast_input:", e)
        return {}

def classify_science_olympiad_input(current_step, user_message):
    prompt = (
        f"Current Science Olympiad step: {current_step}\nUser message: '{user_message}'\n"
        "Return a JSON with key 'answer' (yes or no) or key 'event_category' (study, lab, build)."
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Classify user input for the Science Olympiad workflow."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=50,
            temperature=0.0
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        print("Error in classify_science_olympiad_input:", e)
        return {}

def classify_volunteering_input(current_step, user_message):
    prompt = (
        f"Current Volunteering step: {current_step}\nUser message: '{user_message}'\n"
        "Return a JSON with key 'answer' (yes or no) or key 'path' (existing, one-time, local, nonprofit)."
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Classify user input for the Volunteering workflow."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=50,
            temperature=0.0
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        print("Error in classify_volunteering_input:", e)
        return {}

def classify_research_input(current_step, user_message):
    prompt = (
        f"Current Research step: {current_step}\nUser message: '{user_message}'\n"
        "For steps 'step1_intro' and 'step2_types', return a JSON with key 'answer' (yes or no). "
        "For 'step3_mentor', return a JSON with key 'option' with value 'mentor' or 'jump'."
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Classify user input for the Research workflow."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=50,
            temperature=0.0
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        print("Error in classify_research_input:", e)
        return {}

def detect_research_request(user_message):
    return "research" in user_message.lower()

def detect_deca_request(user_message):
    return "deca" in user_message.lower()

def detect_mun_request(user_message):
    return "mun" in user_message.lower()

def detect_podcast_request(user_message):
    return "podcast" in user_message.lower()

def detect_science_olympiad_request(user_message):
    return "science olympiad" in user_message.lower()

def detect_volunteering_request(user_message):
    keywords = ["volunteer", "nonprofit", "volunteering"]
    return any(word in user_message.lower() for word in keywords)

# -------------------------------
# EXTRACT_GOALS_FROM_TEXT FUNCTION
# -------------------------------
def extract_goals_from_text(text):
    """
    Uses a two-pass approach with GPT to extract actionable goal recommendations from the provided text.
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
        # Post-process output if necessary
        if len(output) > 10:
            output = output[8:]
        if len(output) > 3:
            output = output[:len(output)-3]
        goals = json.loads(output)
        if isinstance(goals, list):
            return goals
    except Exception as e:
        print("Error in fallback goal extraction:", e)
    return []

# -------------------------------
# FUNCTIONS TO GET/UPDATE WORKFLOW STATE FROM FIREBASE
# -------------------------------
def get_workflow_state(student_id):
    student = get_student_data(student_id)
    if student is not None:
        if "workflow_state" not in student:
            student["workflow_state"] = {
                "deca_stage": "none",
                "mun_stage": "none",
                "podcast_stage": "none",
                "science_olympiad_stage": "none",
                "volunteering_stage": "none",
                "research_state": "none"
            }
            update_student_data(student_id, {"workflow_state": student["workflow_state"]})
        return student["workflow_state"]
    return None

# -------------------------------
# WORKFLOW PROCESSING FUNCTIONS
# -------------------------------
def process_research_workflow(student_info, workflow_state, user_message):
    current_step = workflow_state.get('research_state', 'none')
    if current_step == 'none':
        workflow_state['research_state'] = 'step1_intro'
        return generate_workflow_response(RESEARCH_WORKFLOW['step1_intro']['prompt'], student_info, user_message)
    if current_step == 'step1_intro':
        classification = classify_research_input('step1_intro', user_message)
        if classification.get('answer') == 'yes':
            workflow_state['research_state'] = 'step2_types'
            return generate_workflow_response(RESEARCH_WORKFLOW['step2_types']['prompt'], student_info, user_message)
        elif classification.get('answer') == 'no':
            return generate_workflow_response("Let's revisit the potential research fields. Do any of these topics interest you? (Yes/No)", student_info, user_message)
        else:
            return "Please respond with Yes or No regarding your interest in the suggested research fields."
    if current_step == 'step2_types':
        classification = classify_research_input('step2_types', user_message)
        if classification.get('answer') == 'yes':
            workflow_state['research_state'] = 'step3_mentor'
            return generate_workflow_response(RESEARCH_WORKFLOW['step3_mentor']['prompt'], student_info, user_message)
        elif classification.get('answer') == 'no':
            workflow_state['research_state'] = 'step3_mentor'
            return generate_workflow_response("Alright, let's move forward with your research journey. " + RESEARCH_WORKFLOW['step3_mentor']['prompt'], student_info, user_message)
        else:
            return "Please respond with Yes or No regarding your interest in the suggested research paths."
    if current_step == 'step3_mentor':
        classification = classify_research_input('step3_mentor', user_message)
        if classification.get('option') == 'mentor':
            workflow_state['research_state'] = 'mentor'
            return generate_workflow_response("Connecting you with a research mentor. Please wait...", student_info, user_message)
        elif classification.get('option') == 'jump':
            workflow_state['research_state'] = 'step4_details'
            return generate_workflow_response(RESEARCH_WORKFLOW['step4_details']['prompt'], student_info, user_message)
        else:
            return "Please specify if you want to speak to a mentor or jump straight into your research journey. (Mentor/Jump)"
    if current_step == 'step4_details':
        return generate_workflow_response("Let's finalize your research plan. Please review the details and confirm your next steps.", student_info, user_message)
    return "Research workflow processing complete for now."

def process_deca_workflow(student_info, workflow_state, user_message):
    current_step = workflow_state.get('deca_stage', 'none')
    if current_step == 'none':
        workflow_state['deca_stage'] = 'step1_join'
        return generate_workflow_response(DECA_WORKFLOW['step1_join']['prompt'], student_info, user_message)
    if current_step == 'step1_join':
        classification = classify_deca_input('step1_join', user_message)
        if classification.get('answer') == 'yes':
            workflow_state['deca_stage'] = 'step2_event_types'
            return generate_workflow_response(DECA_WORKFLOW['step1_join']['response_if_yes'], student_info, user_message)
        elif classification.get('answer') == 'no':
            workflow_state['deca_stage'] = 'step1_join_no_chapter'
            return generate_workflow_response(DECA_WORKFLOW['step1_join']['response_if_no'], student_info, user_message)
        else:
            return "Could you please confirm if you have a DECA chapter at your school? (Yes/No)"
    if current_step == 'step1_join_no_chapter':
        workflow_state['deca_stage'] = 'step2_event_types'
        prompt = "Proceeding to event selection. " + DECA_WORKFLOW['step2_event_types']['prompt']
        return generate_workflow_response(prompt, student_info, user_message)
    if current_step == 'step2_event_types':
        classification = classify_deca_input('step2_event_types', user_message)
        if classification.get('answer') == 'yes':
            return generate_workflow_response(DECA_WORKFLOW['step2_event_types']['response_if_yes'], student_info, user_message)
        elif classification.get('answer') == 'no':
            workflow_state['deca_stage'] = 'step3_choose_event'
            return generate_workflow_response(DECA_WORKFLOW['step2_event_types']['response_if_no'], student_info, user_message)
        elif classification.get('event_type'):
            event_type = classification.get('event_type')
            if event_type == 'roleplay':
                workflow_state['deca_stage'] = 'step3_roleplay'
                return generate_workflow_response(DECA_WORKFLOW['step3_roleplay']['prompt'], student_info, user_message)
            elif event_type == 'prepared':
                workflow_state['deca_stage'] = 'step3_prepared'
                return generate_workflow_response(DECA_WORKFLOW['step3_prepared']['prompt'], student_info, user_message)
            elif event_type == 'online':
                workflow_state['deca_stage'] = 'step3_online'
                return generate_workflow_response(DECA_WORKFLOW['step3_online']['prompt'], student_info, user_message)
            else:
                return "Please specify whether you're interested in roleplay, prepared, or online events."
        else:
            return "Could you clarify your choice for DECA event types?"
    return "DECA workflow processing complete for now."

def process_mun_workflow(student_info, workflow_state, user_message):
    current_step = workflow_state.get('mun_stage', 'none')
    if current_step == 'none':
        workflow_state['mun_stage'] = 'step1_join'
        return generate_workflow_response(MUN_WORKFLOW['step1_join']['prompt'], student_info, user_message)
    if current_step == 'step1_join':
        classification = classify_mun_input('step1_join', user_message)
        if classification.get('answer') == 'yes':
            workflow_state['mun_stage'] = 'step2_committees'
            return generate_workflow_response(MUN_WORKFLOW['step1_join']['response_if_yes'], student_info, user_message)
        elif classification.get('answer') == 'no':
            workflow_state['mun_stage'] = 'step1_join_no_club'
            return generate_workflow_response(MUN_WORKFLOW['step1_join']['response_if_no'], student_info, user_message)
        else:
            return "Please confirm if you have an MUN club at your school. (Yes/No)"
    if current_step == 'step1_join_no_club':
        workflow_state['mun_stage'] = 'step2_committees'
        prompt = "Proceeding to committee selection. " + MUN_WORKFLOW['step2_committees']['prompt']
        return generate_workflow_response(prompt, student_info, user_message)
    if current_step == 'step2_committees':
        classification = classify_mun_input('step2_committees', user_message)
        if classification.get('answer') == 'yes':
            return generate_workflow_response(MUN_WORKFLOW['step2_committees']['response_if_yes'], student_info, user_message)
        elif classification.get('answer') == 'no':
            workflow_state['mun_stage'] = 'step3_research'
            prompt = "Let's move on to MUN preparation. " + MUN_WORKFLOW['step2_committees']['response_if_no']
            return generate_workflow_response(prompt, student_info, user_message)
        elif classification.get('committee'):
            workflow_state['mun_stage'] = 'step3_research'
            committee = classification.get('committee')
            prompt = f"Great choice with the {committee} committee. Let's proceed with research and writing."
            return generate_workflow_response(prompt, student_info, user_message)
        else:
            return "Please specify which MUN committee interests you."
    return "MUN workflow processing complete for now."

def process_podcast_workflow(student_info, workflow_state, user_message):
    current_step = workflow_state.get('podcast_stage', 'none')
    if current_step == 'none':
        workflow_state['podcast_stage'] = 'step1_concept'
        return generate_workflow_response(PODCAST_WORKFLOW['step1_concept']['prompt'], student_info, user_message)
    if current_step == 'step1_concept':
        classification = classify_podcast_input('step1_concept', user_message)
        if classification.get('answer') == 'yes':
            workflow_state['podcast_stage'] = 'step2_format'
            return generate_workflow_response(PODCAST_WORKFLOW['step1_concept']['response_if_yes'], student_info, user_message)
        elif classification.get('answer') == 'no':
            return "Let's brainstorm some podcast ideas. What topics do you love talking about?"
        else:
            return "Could you confirm if you have a podcast concept? (Yes/No)"
    if current_step == 'step2_format':
        classification = classify_podcast_input('step2_format', user_message)
        if classification.get('choice'):
            choice = classification.get('choice')
            workflow_state['podcast_stage'] = 'step3_equipment'
            prompt = f"You selected the {choice} format. " + PODCAST_WORKFLOW['step2_format']['prompt']
            return generate_workflow_response(prompt, student_info, user_message)
        elif classification.get('answer') == 'yes':
            workflow_state['podcast_stage'] = 'step3_equipment'
            return generate_workflow_response(PODCAST_WORKFLOW['step2_format']['response_if_yes'], student_info, user_message)
        elif classification.get('answer') == 'no':
            return "Which podcast format do you prefer? (solo, co-hosted, interview, narrative, hybrid)"
        else:
            return "Please clarify your choice of podcast format."
    return "Podcast workflow processing complete for now."

def process_science_olympiad_workflow(student_info, workflow_state, user_message):
    current_step = workflow_state.get('science_olympiad_stage', 'none')
    if current_step == 'none':
        workflow_state['science_olympiad_stage'] = 'step1_categories'
        return generate_workflow_response(SCI_OLY_WORKFLOW['step1_categories']['prompt'], student_info, user_message)
    if current_step == 'step1_categories':
        classification = classify_science_olympiad_input('step1_categories', user_message)
        if classification.get('answer') == 'yes':
            return generate_workflow_response("Please provide a detailed explanation of each Science Olympiad event type based on the official rulebook.", student_info, user_message)
        elif classification.get('answer') == 'no':
            workflow_state['science_olympiad_stage'] = 'step2_select_event'
            return generate_workflow_response(SCI_OLY_WORKFLOW['step2_select_event']['prompt'], student_info, user_message)
        elif classification.get('event_category'):
            event_cat = classification.get('event_category')
            workflow_state['science_olympiad_stage'] = 'step2_select_event'
            prompt = f"You selected {event_cat} events. " + SCI_OLY_WORKFLOW['step2_select_event']['prompt']
            return generate_workflow_response(prompt, student_info, user_message)
        else:
            return "Could you clarify which Science Olympiad event category interests you? (Study, Lab, or Build) or do you want a detailed explanation? (Yes/No)"
    if current_step == 'step2_select_event':
        classification = classify_science_olympiad_input('step2_select_event', user_message)
        if classification.get('answer') == 'yes':
            return generate_workflow_response("Based on your interests and strengths, I recommend a specific Science Olympiad event. Could you provide more details about your strengths?", student_info, user_message)
        elif classification.get('answer') == 'no':
            workflow_state['science_olympiad_stage'] = 'step3_preparation'
            return generate_workflow_response(SCI_OLY_WORKFLOW['step3_preparation']['prompt'], student_info, user_message)
        else:
            return "Would you like a personalized event recommendation? (Yes/No)"
    if current_step == 'step3_preparation':
        classification = classify_science_olympiad_input('step3_preparation', user_message)
        if classification.get('answer') in ['yes', 'no']:
            workflow_state['science_olympiad_stage'] = 'step4_strategies'
            return generate_workflow_response(SCI_OLY_WORKFLOW['step4_strategies']['prompt'], student_info, user_message)
        else:
            return "Do you need sample tests, lab guides, or design tips for preparation? (Yes/No)"
    if current_step == 'step4_strategies':
        classification = classify_science_olympiad_input('step4_strategies', user_message)
        if classification.get('answer') == 'yes':
            return generate_workflow_response("Providing event-specific strategies and past competition insights.", student_info, user_message)
        elif classification.get('answer') == 'no':
            workflow_state['science_olympiad_stage'] = 'step5_competition_day'
            return generate_workflow_response(SCI_OLY_WORKFLOW['step5_competition_day']['prompt'], student_info, user_message)
        else:
            return "Would you like event-specific strategies or past competition insights? (Yes/No)"
    if current_step == 'step5_competition_day':
        classification = classify_science_olympiad_input('step5_competition_day', user_message)
        if classification.get('answer') == 'yes':
            return generate_workflow_response("Here are additional tips and details for competition day.", student_info, user_message)
        elif classification.get('answer') == 'no':
            return generate_workflow_response("Great! You're all set for your Science Olympiad competition.", student_info, user_message)
        else:
            return "Please confirm if you need further details for competition day. (Yes/No)"
    return "Science Olympiad workflow processing complete for now."

def process_volunteering_workflow(student_info, workflow_state, user_message):
    current_step = workflow_state.get('volunteering_stage', 'none')
    if current_step == 'none':
        workflow_state['volunteering_stage'] = 'step1_interests'
        return generate_workflow_response(VOLUNTEERING_WORKFLOW['step1_interests']['prompt'], student_info, user_message)
    if current_step == 'step1_interests':
        classification = classify_volunteering_input('step1_interests', user_message)
        if classification.get('answer') == 'yes' or classification.get('path'):
            workflow_state['volunteering_stage'] = 'step2_types'
            return generate_workflow_response(VOLUNTEERING_WORKFLOW['step2_types']['prompt'], student_info, user_message)
        else:
            return "Keep brainstorming causes or share a few ideas that interest you."
    if current_step == 'step2_types':
        classification = classify_volunteering_input('step2_types', user_message)
        if classification.get('path') in ['existing', 'one-time']:
            workflow_state['volunteering_stage'] = 'step4_existing'
            return generate_workflow_response(VOLUNTEERING_WORKFLOW['step4_existing']['prompt'], student_info, user_message)
        elif classification.get('path') == 'local':
            workflow_state['volunteering_stage'] = 'step5_local_initiative'
            return generate_workflow_response(VOLUNTEERING_WORKFLOW['step5_local_initiative']['prompt'], student_info, user_message)
        elif classification.get('path') == 'nonprofit':
            workflow_state['volunteering_stage'] = 'step6_nonprofit'
            return generate_workflow_response(VOLUNTEERING_WORKFLOW['step6_nonprofit']['prompt'], student_info, user_message)
        else:
            return "Which volunteering path interests you? (existing, one-time, local, nonprofit)"
    if current_step == 'step4_existing':
        return generate_workflow_response(VOLUNTEERING_WORKFLOW['step4_existing']['prompt'], student_info, user_message)
    if current_step == 'step5_local_initiative':
        return generate_workflow_response(VOLUNTEERING_WORKFLOW['step5_local_initiative']['prompt'], student_info, user_message)
    if current_step == 'step6_nonprofit':
        return generate_workflow_response(VOLUNTEERING_WORKFLOW['step6_nonprofit']['prompt'], student_info, user_message)
    if current_step == 'step7_considerations':
        return generate_workflow_response(VOLUNTEERING_WORKFLOW['step7_considerations']['prompt'], student_info, user_message)
    return "Volunteering workflow processing complete for now."

# -------------------------------
# UTILITY FUNCTIONS (Conversation, Goals, etc.)
# -------------------------------
def _chat_with_athena(student_info, conversation, conversation_summary):
    try:
        messages_for_model = generate_messages(student_info, conversation, conversation_summary)
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=messages_for_model,
            max_tokens=300,
            temperature=0.8
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error in AI response: {str(e)}"
def parse_onboarding_info(questions):
    """
    Uses GPT to summarize and contextualize a student's onboarding answers into the appropriate student schema fields.
    Expects 'questions' as a list of answers corresponding to:
      - Intended Major
      - Creativity
      - Service
      - Skill/Talent
      - Extracurriculars
    Returns a JSON mapping with keys: intended_major, creativity, service, skill_talent, extracurriculars, leadership.
    Leadership may be empty if not provided.
    """
    prompt = (
        "Given the following onboarding answers from a high school student, please summarize and assign the information to the appropriate fields "
        "in a student schema. The fields are defined as follows:\n"
        "- intended_major: What the student is interested in studying and whether they have explored that interest.\n"
        "- creativity: The student's creative side, including hobbies or passions for creative expression.\n"
        "- service: The student's volunteering experiences.\n"
        "- skill_talent: The student's greatest skill or talent.\n"
        "- extracurriculars: The student's current extracurricular activities.\n"
        "- leadership: Any leadership experiences (if available).\n\n"
        "The student's answers are:\n"
        f"{json.dumps(questions)}\n\n"
        "Output a JSON object with keys: intended_major, creativity, service, skill_talent, extracurriculars, leadership."
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an assistant that maps onboarding answers to a student schema."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.3
        )
        output = response.choices[0].message.content.strip()
        mapping = json.loads(output)
        return mapping
    except Exception as e:
        print("Error in parse_onboarding_info:", e)
        return {}

def update_student_topics(student_id, new_topic_sentence):
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

def shorten_topic_sentence(topic, chat_partner):
    return f"Talked to {chat_partner} about: {topic[:50]}..."

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

# -------------------------------
# NEW API ENDPOINT TO UPDATE STUDENT SCHEMA (Onboarding with Contextual Mapping)
# -------------------------------
# -------------------------------
# MAIN FLASK API ENDPOINTS
# -------------------------------
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
                'topics': [],
                'workflow_state': {
                    "deca_stage": "none",
                    "mun_stage": "none",
                    "podcast_stage": "none",
                    "science_olympiad_stage": "none",
                    "volunteering_stage": "none",
                    "research_state": "none"
                }
            }
        else:
            for key in ['grade', 'future_study', 'deep_interest', 'current_extracurriculars', 'favorite_courses']:
                if key in data:
                    student_data[key] = data[key]
            if "workflow_state" not in student_data:
                student_data["workflow_state"] = {
                    "deca_stage": "none",
                    "mun_stage": "none",
                    "podcast_stage": "none",
                    "science_olympiad_stage": "none",
                    "volunteering_stage": "none",
                    "research_state": "none"
                }

        save_student_data(student_id, student_data)
        return jsonify({"student_id": student_id, "message": "Student created/updated successfully"})
    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/goals/<student_id>', methods=['GET'])
def get_goals_endpoint(student_id):
    student_id = student_id.strip().lower()
    student_info = get_student_data(student_id)
    if not student_info:
        return jsonify({"error": "Student not found"}), 404
    goals = student_info.get("goals", [])
    return jsonify({"goals": goals})

@app.route('/api/starters/<student_id>', methods=['GET'])
def get_conversation_starters_endpoint(student_id):
    try:
        student_id = student_id.strip().lower()
        student_info = get_student_data(student_id)
        if not student_info:
            return jsonify({"error": "Student not found"}), 404
        conversation = student_info.get("last_conversation", [])
        starters = generate_conversation_starters(student_info, conversation)
        return jsonify({"starters": starters})
    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

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
                'topics': [],
                'workflow_state': {
                    "deca_stage": "none",
                    "mun_stage": "none",
                    "podcast_stage": "none",
                    "science_olympiad_stage": "none",
                    "volunteering_stage": "none",
                    "research_state": "none"
                }
            }
            save_student_data(student_id, student_info)

        workflow_state = student_info.get("workflow_state", {
            "deca_stage": "none",
            "mun_stage": "none",
            "podcast_stage": "none",
            "science_olympiad_stage": "none",
            "volunteering_stage": "none",
            "research_state": "none"
        })

        conversation = student_info.get("last_conversation", [])
        conversation_summary = student_info.get("conversation_summary", "")

        workflow_response = None
        if detect_research_request(user_message):
            workflow_response = process_research_workflow(student_info, workflow_state, user_message)
        elif detect_deca_request(user_message):
            workflow_response = process_deca_workflow(student_info, workflow_state, user_message)
        elif detect_mun_request(user_message):
            workflow_response = process_mun_workflow(student_info, workflow_state, user_message)
        elif detect_podcast_request(user_message):
            workflow_response = process_podcast_workflow(student_info, workflow_state, user_message)
        elif detect_science_olympiad_request(user_message):
            workflow_response = process_science_olympiad_workflow(student_info, workflow_state, user_message)
        elif detect_volunteering_request(user_message):
            workflow_response = process_volunteering_workflow(student_info, workflow_state, user_message)
        else:
            workflow_response = None

        if workflow_response is not None:
            conversation.append({'role': 'assistant', 'content': workflow_response})
            update_student_data(student_id, {
                "last_conversation": conversation,
                "conversation_summary": conversation_summary,
                "workflow_state": workflow_state
            })
            return jsonify({"conversation": conversation, "last_response": workflow_response, "mentor_id": None})

        conversation.append({'role': 'user', 'content': user_message})
        conversation, conversation_summary = optimize_conversation_history(conversation, conversation_summary)
        short_topic = shorten_topic_sentence(user_message, "Athena")
        updated_topics = update_student_topics(student_id, short_topic)
        if updated_topics is not None:
            student_info["topics"] = updated_topics

        assistant_message = _chat_with_athena(student_info, conversation, conversation_summary)
        conversation.append({'role': 'assistant', 'content': assistant_message})

        if student_info.get('goal_cooldown', 0) == 0:
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
                    conversation.append({'role': 'assistant', 'content': f"✅ I’ve officially added **'{goal}'** to your goals!"})
            if new_goals:
                student_info['goal_cooldown'] = 5
        else:
            student_info['goal_cooldown'] = student_info.get('goal_cooldown', 1) - 1

        if student_info.get('mentor_cooldown', 0) > 0:
            student_info['mentor_cooldown'] = student_info.get('mentor_cooldown', 1) - 1
        else:
            if is_explicit_mentor_request(user_message) or "mentor" in user_message.lower():
                best_mentor, best_score = recommend_mentor(user_message, student_info)
                if best_mentor:
                    conversation.append({'role': 'assistant', 'content': f"Mentor Recommendation: **{best_mentor}** (Score: {best_score:.2f})\n\n{generate_mentor_reason(best_mentor, user_message)}"})
                    student_info['mentor_cooldown'] = 3

        update_student_data(student_id, {
            "last_conversation": conversation,
            "conversation_summary": conversation_summary,
            "workflow_state": workflow_state,
            "goal_cooldown": student_info.get('goal_cooldown', 0),
            "mentor_cooldown": student_info.get('mentor_cooldown', 0)
        })
        return jsonify({"conversation": conversation, "last_response": conversation[-1]['content'], "mentor_id": None})
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
        f"Create a concise and engaging 3-4 sentence biography for a student with the following details:\n\n"
        f"Name: {structured_info['name']}\n"
        f"Grade: {structured_info['grade']}\n"
        f"Future Study Interests: {structured_info['future_study']}\n"
        f"Deep Interests: {structured_info['deep_interest']}\n"
        f"Current Extracurriculars: {structured_info['current_extracurriculars']}\n"
        f"Favorite Courses: {structured_info['favorite_courses']}\n"
        f"Competitions: {', '.join(structured_info['competitions']) if structured_info['competitions'] else 'None'}\n"
        f"Goals: {', '.join(structured_info['goals']) if structured_info['goals'] else 'None'}\n\n"
        "Ensure the summary is natural, engaging, and informative."
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
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
def get_topics_endpoint(student_id):
    student_id = student_id.strip().lower()
    student_info = get_student_data(student_id)
    if not student_info:
        return jsonify({"error": "Student not found"}), 404
    topics = student_info.get("topics", [])
    return jsonify({"topics": topics})

# -------------------------------
# NEW API ENDPOINT TO UPDATE STUDENT SCHEMA (Onboarding with Contextual Mapping)
# -------------------------------
@app.route('/api/update_student_schema', methods=['POST'])
def update_student_schema():
    """
    Expects a JSON payload with:
    {
      "name": "Kundan Singh",
      "email": "ki@gmail.com",
      "question": [
          "Answer to: What are you interested in studying? Have you explored that interest yet? If so, how?",
          "Answer to: Tell me about your creative side. Any hobbies or passions you enjoy to express this?",
          "Answer to: Describe any volunteering experiences you have.",
          "Answer to: What’s your greatest skill/talent?",
          "Answer to: Briefly describe your current extracurriculars."
      ],
      "grade": "12"
    }
    This endpoint uses GPT to contextualize and summarize the onboarding answers into the student schema fields:
      - intended_major, creativity, service, skill_talent, extracurriculars, leadership.
    Leadership is left unchanged if not provided.
    """
    try:
        data = request.get_json()
        print(data)
        if not data.get("name") or not data.get("email") or "question" not in data or not data.get("grade"):
            return jsonify({"error": "Missing required fields: name, email, question, and grade are required."}), 400

        student_id = data.get("name").strip().lower().replace(" ","")
        email = data.get("email")
        questions = data.get("question")
        grade = data.get("grade")

        # Use GPT to parse and contextualize the onboarding info.
        parsed_info = parse_onboarding_info(questions)

        new_schema = {
            "email": email,
            "grade": grade,
            "intended_major": parsed_info.get("intended_major", ""),
            "creativity": parsed_info.get("creativity", ""),
            "service": parsed_info.get("service", ""),
            "skill_talent": parsed_info.get("skill_talent", ""),
            "extracurriculars": parsed_info.get("extracurriculars", ""),
            "leadership": parsed_info.get("leadership", ""),
            "competitions": "",
            "notes": "",
            "goals": [],
            "conversation_summary": "",
            "last_conversation": [],
            "topics": [],
            "workflow_state": {
                "deca_stage": "none",
                "mun_stage": "none",
                "podcast_stage": "none",
                "science_olympiad_stage": "none",
                "volunteering_stage": "none",
                "research_state": "none"
            }
        }
        update_student_data(student_id, new_schema)
        return jsonify({"message": "Student schema updated successfully", "student_id": student_id})
    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

# -------------------------------
# MAIN ENTRY POINT
# -------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
