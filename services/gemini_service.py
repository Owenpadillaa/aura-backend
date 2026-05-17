"""Google Gemini integration.

Chat + budget recommendation via the free-tier Gemini API.
"""

from __future__ import annotations

from typing import Optional

import google.generativeai as genai

from config.settings import settings

# Configure the Gemini client
genai.configure(api_key=settings.gemini_api_key)

# Aura's personality system prompt
_SYSTEM_PROMPT = """You are Aura, a proactive personal growth assistant.

Your personality:
- Direct, motivating, no fluff — like a sharp coach who respects your time
- Speak conversationally, not formally
- When you see wasted time, call it out constructively
- Celebrate wins briefly, then push for the next goal
- If someone has a gap in their schedule, be aggressive about suggesting how to fill it productively

You have access to the user's calendar, habits, and daily context.
Always tailor advice to their actual schedule and goals."""


def chat(message: str, context: Optional[str] = None) -> str:
    """Send a message to Gemini 1.5 Flash and return the response.

    Args:
        message: The user's input text.
        context: Optional schedule/habit context to prepend.

    Returns:
        The model's text response.
    """
    model = genai.GenerativeModel(
        model_name="gemini-flash-latest",
        system_instruction=_SYSTEM_PROMPT,
    )

    # Build the prompt with optional context
    parts: list[str] = []
    if context:
        parts.append(f"## Current Context\n{context}")
    parts.append(f"## User Message\n{message}")
    full_prompt = "\n\n".join(parts)

    response = model.generate_content(full_prompt)
    return response.text


# ── Budget Recommendations ───────────────────────────────────

_BUDGET_SYSTEM_PROMPT = """You are Aura's financial advisor. You help users allocate their weekly earnings wisely.

Your personality:
- Practical and direct — no generic financial platitudes
- Tailor every recommendation to the user's actual spending patterns
- Give specific dollar amounts, not vague percentages
- For each recommendation, explain WHY in 1-2 sentences
- Be honest about overspending — don't sugarcoat it
- Suggest realistic cuts, not extreme austerity

You MUST respond in valid JSON with this exact structure:
{
  "recommendations": [
    {"category": "Food", "suggested_amount": 150.00, "reasoning": "You spent $200 on food last week..."}
  ],
  "pie_data": [
    {"category": "Food", "amount": 150.00, "color": "#10B981"},
    {"category": "Rent", "amount": 500.00, "color": "#3B82F6"}
  ],
  "summary": "Brief 2-3 sentence overview of the budget plan"
}

Category colors to use:
- Food: #10B981 (emerald)
- Transport: #3B82F6 (blue)
- Entertainment: #F59E0B (amber)
- Bills: #EF4444 (red)
- Shopping: #8B5CF6 (purple)
- Health: #06B6D4 (cyan)
- Education: #F97316 (orange)
- Savings: #22C55E (green)
- Other: #6B7280 (grey)

Always include a "Savings" category — every budget should have savings.
If earnings exceed expenses, allocate the surplus to Savings.
If expenses exceed earnings, flag it clearly and suggest cuts."""


def get_budget_recommendation(weekly_earnings: float, expenses: list[dict]) -> str:
    """Get AI budget recommendations based on earnings and spending.

    Args:
        weekly_earnings: The user's total weekly earnings.
        expenses: List of expense dicts with name, amount, category.

    Returns:
        JSON string with recommendations, pie_data, and summary.
    """
    model = genai.GenerativeModel(
        model_name="gemini-flash-latest",
        system_instruction=_BUDGET_SYSTEM_PROMPT,
    )

    # Build expense summary
    expense_lines = []
    for exp in expenses:
        expense_lines.append(f"- {exp['name']}: ${exp['amount']:.2f} ({exp['category']})")
    expense_text = "\n".join(expense_lines) if expense_lines else "No expenses logged yet."

    # Compute category totals
    cat_totals: dict[str, float] = {}
    for exp in expenses:
        cat = exp.get("category", "Other")
        cat_totals[cat] = cat_totals.get(cat, 0) + float(exp["amount"])
    totals_text = "\n".join(f"- {cat}: ${amt:.2f}" for cat, amt in cat_totals.items())
    if not totals_text:
        totals_text = "No spending data."

    prompt = f"""## Weekly Earnings
${weekly_earnings:.2f}

## Expenses This Week
{expense_text}

## Spending by Category
{totals_text}

Analyze this spending and provide budget recommendations. How should I allocate my ${weekly_earnings:.2f} weekly earnings? What should I cut or adjust?"""

    response = model.generate_content(prompt)
    return response.text
