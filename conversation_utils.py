import openai
import bleach
import markdown2
import json

CONVERSATION_SUMMARIZE_THRESHOLD = 8
SUMMARIZER_MODEL = "gpt-3.5-turbo"

import re

def detect_goal_creation(assistant_message):
    """Detects if Athena's response suggests a goal."""
    goal_patterns = [
        r"you should try to (.+)",
        r"it would be great if you could (.+)",
        r"consider working on (.+)",
        r"maybe set a goal to (.+)"
    ]

    for pattern in goal_patterns:
        match = re.search(pattern, assistant_message, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    return None  # No goal detected

def summarize_conversation(conversation):
    text_to_summarize = ""
    for msg in conversation:
        role = "User" if msg['role'] == 'user' else "Athena"
        text_to_summarize += f"{role}: {msg['content']}\n"

    system_msg = (
        "You are a summarizing assistant. Summarize the conversation below in 100 words or less, "
        "focusing on key points and the student's interests or questions."
    )

    response = openai.chat.completions.create(
        model=SUMMARIZER_MODEL,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": text_to_summarize}
        ],
        max_tokens=150,
        temperature=0.0
    )
    summary = response.choices[0].message.content.strip()
    return summary


def optimize_conversation_history(conversation, current_summary, threshold=5):
    if len(conversation) >= threshold:
        # Concatenate the conversation messages into one text
        convo_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in conversation])
        prompt = (
            "Summarize the following conversation concisely, capturing key topics, decisions, and important details:\n"
            + convo_text
        )
        try:
            response = openai.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a conversation summarizer."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=150,
                temperature=0.5
            )
            new_summary = response.choices[0].message.content.strip()
            print("New conversation summary generated:", new_summary)
            # Optionally, you can replace the conversation with just the summary
            conversation = [{"role": "system", "content": "Conversation summary: " + new_summary}]
            return conversation, new_summary
        except Exception as e:
            print(f"Error generating conversation summary: {e}")
            return conversation, current_summary
    return conversation, current_summary

def generate_messages(student_info, conversation, conversation_summary):
    system_prompt = (
        "You are Athena, a friendly and supportive college counselor. "
        "Keep responses concise (3-5 sentences), casual, and empathetic. "
        "Ask clarifying questions if needed.\n\n"
        "--- Student's Profile ---\n"
        f"Name: {student_info.get('name','')}\n"
        f"Grade: {student_info.get('grade','')}\n"
        f"Future Study Interests: {student_info.get('future_study','')}\n"
        f"Deep Interests: {student_info.get('deep_interest','')}\n"
        f"Unique Traits: {student_info.get('unique_something','')}\n"
        f"Current Extracurriculars: {student_info.get('current_extracurriculars','')}\n"
        f"Favorite Courses: {student_info.get('favorite_courses','')}\n"
        "\n--- Summary so far ---\n"
        f"{conversation_summary if conversation_summary else '(no summary yet)'}\n"
    )

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation)
    return messages

def generate_conversation_starters(student_info, conversation):
    system_prompt = (
        "You are Athena, a friendly and supportive college counselor. Generate 3 PERSONALIZED (using student info) conversation starters "
        "the student might ask next to advance their college goals AND CONTINUE THE CONVERSATION!. Focus on the student's profile "
        "and their recent conversation. Output as a numbered list. "
        "Do not prefix with 'Athena:' – these are the student's potential questions."
    )

    profile_part = (
        f"Student's Profile:\n"
        f"- Name: {student_info.get('name','')}\n"
        f"- Grade: {student_info.get('grade','')}\n"
        f"- Future Study: {student_info.get('future_study','')}\n"
        f"- Deep Interests: {student_info.get('deep_interest','')}\n"
        f"- Unique Traits: {student_info.get('unique_something','')}\n"
        f"- Current Extracurriculars: {student_info.get('current_extracurriculars','')}\n"
        f"- Favorite Courses: {student_info.get('favorite_courses','')}\n"
    )

    recent_convo_text = ""
    for msg in conversation[-5:]:
        role = "Student" if msg['role'] == 'user' else 'Athena'
        recent_convo_text += f"{role}: {msg['content']}\n"

    assistant_prompt = profile_part + "\nRecent Conversation:\n" + recent_convo_text

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "assistant", "content": assistant_prompt}
    ]

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        max_tokens=300,
        temperature=0.7,
        n=1
    )
    text = response.choices[0].message.content.strip()
    starters = []
    for line in text.split('\n'):
        line_stripped = line.strip().strip('"')
        if line_stripped[:2] in ("1.", "2.", "3."):
            question = line_stripped[line_stripped.find('.')+1:].strip()
            starters.append(question)
    return starters[:3]

def render_markdown(content):
    html_content = markdown2.markdown(content)
    clean_html = bleach.clean(
        html_content,
        tags=['p', 'strong', 'em', 'ul', 'ol', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'a', 'b', 'i'],
        attributes={'a': ['href', 'title']},
        strip=True
    )
    return clean_html

def parse_new_student_info(
    user_message: str,
    current_student_data: dict,
    conversation_summary: str
) -> dict:
    """
    Uses GPT to extract new or updated student info from the user's latest message.
    Also handles disclaimers or conflicting data. Returns a dict with:
        {
            "updates": { ...fields to update... },
            "disclaimers": "...any disclaimers or conflicts..."
        }
    If no changes, returns: {"updates": {}, "disclaimers": ""}
    """

    system_prompt = """
You are an expert system that parses updates to a student's profile based on a conversation summary and the user's latest message. 
The student's current data is provided. The user may add or update fields like:
- grade
- future_study
- deep_interest
- current_extracurriculars
- favorite_courses
- competitions
- notes
- goals

You must also detect disclaimers or conflicting info. For example:
- "I used to be in Math Club but I'm no longer in it" => disclaimers: "User left Math Club."
- "I might want to do pre-med, but I'm not sure yet." => disclaimers: "User is uncertain about future_study."

Output ONLY valid JSON. The JSON should have two keys:
  "updates": a JSON object of new or changed fields
  "disclaimers": a string summarizing disclaimers or conflicting info

If no changes are detected, return: {"updates": {}, "disclaimers": ""}

Do not include extraneous text, just JSON.
"""

    user_prompt = f"""
Conversation summary so far: {conversation_summary}

Current student data: {json.dumps(current_student_data, ensure_ascii=False)}
User's latest message: "{user_message}"

Follow the system prompt. Output valid JSON only.
"""

    try:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=300,
            temperature=0.0
        )

        raw_json = response.choices[0].message.content.strip()
        raw_json = raw_json[8:]
        raw_json = raw_json[:len(raw_json) - 3]
        parsed_data = json.loads(raw_json)  # parse GPT's JSON

        # Must at least have "updates" and "disclaimers" keys
        if not isinstance(parsed_data, dict):
            return {"updates": {}, "disclaimers": ""}
        if "updates" not in parsed_data:
            parsed_data["updates"] = {}
        if "disclaimers" not in parsed_data:
            parsed_data["disclaimers"] = ""

        return parsed_data

    except Exception as e:
        print(f"Error extracting student info: {e}")
        return {"updates": {}, "disclaimers": ""}
