import openai
import bleach
import markdown2

CONVERSATION_SUMMARIZE_THRESHOLD = 8
SUMMARIZER_MODEL = "gpt-3.5-turbo"

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

def optimize_conversation_history(conversation, conversation_summary):
    if len(conversation) > CONVERSATION_SUMMARIZE_THRESHOLD:
        new_summary = summarize_conversation(conversation)
        if conversation_summary:
            conversation_summary += " " + new_summary
        else:
            conversation_summary = new_summary
        conversation = conversation[-2:]
    return conversation, conversation_summary

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
        "You are Athena, a friendly and supportive college counselor. Generate 4 conversation starters "
        "the student might ask next to advance their college goals. Focus on the student's profile "
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
        model="gpt-4",
        messages=messages,
        max_tokens=250,
        temperature=0.7,
        n=1
    )
    text = response.choices[0].message.content.strip()
    starters = []
    for line in text.split('\n'):
        line_stripped = line.strip()
        if line_stripped[:2] in ("1.", "2.", "3.", "4."):
            question = line_stripped[line_stripped.find('.')+1:].strip()
            starters.append(question)
    return starters[:4]

def render_markdown(content):
    html_content = markdown2.markdown(content)
    clean_html = bleach.clean(
        html_content,
        tags=['p', 'strong', 'em', 'ul', 'ol', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'a', 'b', 'i'],
        attributes={'a': ['href', 'title']},
        strip=True
    )
    return clean_html
