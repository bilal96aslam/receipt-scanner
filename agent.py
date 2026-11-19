import os
from google import genai
from google.genai import types
from sqlmodel import Session, select
from dotenv import load_dotenv
from database import engine, Receipt

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

tools = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="get_receipts",
            description="Get all receipts for the current user. Use this when the user asks about their spending, receipts, or purchases.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={},
                required=[]
            )
        ),
        types.FunctionDeclaration(
            name="get_total_spent",
            description="Calculate the total amount spent across all receipts for the current user.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={},
                required=[]
            )
        ),
    ])
]

def run_tool(tool_name: str, user_id: int) -> str:
    if tool_name == "get_receipts":
        with Session(engine) as session:
            receipts = session.exec(
                select(Receipt).where(Receipt.user_id == user_id)
            ).all()
            if not receipts:
                return "No receipts found."
            lines = [f"{r.vendor} on {r.date}: ${r.total}" for r in receipts]
            return "\n".join(lines)

    if tool_name == "get_total_spent":
        with Session(engine) as session:
            receipts = session.exec(
                select(Receipt).where(Receipt.user_id == user_id)
            ).all()
            total = sum(r.total for r in receipts)
            return f"Total spent: ${total:.2f} across {len(receipts)} receipts."

    return f"Unknown tool: {tool_name}"


def ask_agent(question: str, user_id: int) -> str:
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=question,
        config=types.GenerateContentConfig(tools=tools)
    )

    candidate = response.candidates[0]
    part = candidate.content.parts[0]

    if part.function_call:
        tool_name = part.function_call.name
        tool_result = run_tool(tool_name, user_id)

        final_response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=[
                types.Content(role="user", parts=[types.Part(text=question)]),
                types.Content(role="model", parts=[part]),
                types.Content(role="user", parts=[
                    types.Part(function_response=types.FunctionResponse(
                        name=tool_name,
                        response={"result": tool_result}
                    ))
                ]),
            ],
            config=types.GenerateContentConfig(tools=tools)
        )
        return final_response.text

    return part.text