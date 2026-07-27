"""
Page: AI Analysis — use Claude or ChatGPT to analyse journal entries and statistics
"""
import streamlit as st
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils.trade_ops import get_journal_entries, get_trades
from utils.statistics import get_trade_stats
from utils.playbook_logic import get_playbooks, get_playbook_compliance_stats
from database import fetch_all


# ── Prompt builders ──────────────────────────────────────────────────────────

def build_stats_context(stats: dict, playbook_stats: list) -> str:
    lines = ["## Trading Performance Summary\n"]
    if stats:
        lines += [
            f"- Total trades: {stats['total_trades']}",
            f"- Win rate: {stats['win_rate']}%",
            f"- Net P&L: {stats['net_pnl']}",
            f"- Profit factor: {stats['profit_factor']}",
            f"- R:R ratio: {stats['rr_ratio']}",
            f"- Expectancy: {stats['expectancy']}",
            f"- Max drawdown: {stats['max_drawdown']}",
            f"- Avg win: {stats['avg_win']} | Avg loss: {stats['avg_loss']}",
            f"- Max win streak: {stats['max_win_streak']} | Max loss streak: {stats['max_loss_streak']}",
        ]
        if stats.get("by_symbol"):
            lines.append("\n### By Symbol")
            for s in stats["by_symbol"]:
                lines.append(f"- {s['symbol']}: {s['trades']} trades, WR {s['win_rate']:.1f}%, P&L {s['total_pnl']:.2f}")
        if stats.get("by_direction"):
            lines.append("\n### Long vs Short")
            for d in stats["by_direction"]:
                lines.append(f"- {d['direction']}: {d['trades']} trades, WR {d['win_rate']:.1f}%, P&L {d['total_pnl']:.2f}")
    
    if playbook_stats:
        lines.append("\n## Playbook Compliance")
        for pb in playbook_stats:
            lines.append(f"\n### {pb.get('playbook_name','Playbook')}")
            lines.append(f"- Total trades: {pb.get('total_trades', 0)}")
            lines.append(f"- Avg risk score: {pb.get('avg_risk_score', 0)}")
            lines.append(f"- Score ↔ P&L correlation: {pb.get('score_pnl_correlation', 0)}")
            for rule in pb.get("rule_stats", []):
                lines.append(f"  - [{rule['type'].upper()}] {rule['name']}: {rule['compliance_pct']}% compliance, "
                             f"P&L when met: {rule['pnl_when_met']:.2f}, P&L when not met: {rule['pnl_when_not_met']:.2f}")
    
    return "\n".join(lines)


def build_journal_context(entries: list, limit: int = 10) -> str:
    lines = [f"## Recent Journal Entries (last {min(len(entries), limit)})\n"]
    for e in entries[:limit]:
        lines.append(f"### {e['entry_type'].upper()} — {e['entry_date']}")
        if e.get("grade"):
            lines.append(f"**Grade:** {e['grade']} | **Mood:** {e.get('mood', '?')}/10")
        if e.get("analysis"):
            lines.append(f"**Analysis:** {e['analysis'][:400]}")
        if e.get("execution"):
            lines.append(f"**Execution:** {e['execution'][:300]}")
        if e.get("psychology"):
            lines.append(f"**Psychology:** {e['psychology'][:300]}")
        if e.get("lessons"):
            lines.append(f"**Lessons:** {e['lessons'][:200]}")
        lines.append("")
    return "\n".join(lines)


SYSTEM_PROMPT = """You are an expert trading coach and performance analyst with deep experience in 
technical analysis, risk management, trader psychology, and systematic trading.

You are analysing a trader's journal and statistics. Your role is to:
1. Identify genuine patterns (both strengths and weaknesses)
2. Give specific, actionable advice — not generic platitudes
3. Point out psychological patterns that may be affecting performance
4. Highlight which setups/rules are actually working vs not
5. Be direct and honest, but constructive

When referencing data, cite specific numbers. Avoid vague generalisations.
Format your response with clear sections using markdown headers.
"""


# ── AI call functions ─────────────────────────────────────────────────────────

def call_claude(api_key: str, prompt: str, model: str = "claude-opus-4-5") -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


def call_openai(api_key: str, prompt: str, model: str = "gpt-4o") -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=2000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
    )
    return resp.choices[0].message.content


# ── Conversation history helper ───────────────────────────────────────────────

def get_chat_history():
    return st.session_state.get("ai_chat_history", [])

def add_to_history(role: str, content: str):
    if "ai_chat_history" not in st.session_state:
        st.session_state["ai_chat_history"] = []
    st.session_state["ai_chat_history"].append({"role": role, "content": content})

def build_conversation_messages(system_context: str, history: list, new_message: str) -> list:
    """Build full message history for multi-turn conversation."""
    messages = []
    # Inject context as first user message if history is empty
    if not history:
        messages.append({
            "role": "user",
            "content": f"Here is my trading data for context:\n\n{system_context}\n\nFirst question: {new_message}"
        })
    else:
        # Re-inject context reminder at start
        messages.append({
            "role": "user",
            "content": f"[Context: trading data provided at start of session]\n\n{new_message if not history else history[0]['content']}"
        })
        messages.append({"role": "assistant", "content": history[0]["content"] if history else ""})
        for msg in history[1:]:
            messages.append(msg)
        messages.append({"role": "user", "content": new_message})
    return messages


def call_claude_chat(api_key: str, messages: list, model: str = "claude-opus-4-5") -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    # Filter to valid roles only
    valid = [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    message = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=valid
    )
    return message.content[0].text


def call_openai_chat(api_key: str, messages: list, model: str = "gpt-4o") -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
    resp = client.chat.completions.create(
        model=model,
        max_tokens=2000,
        messages=full_messages
    )
    return resp.choices[0].message.content


# ── Main page ─────────────────────────────────────────────────────────────────

def show():
    st.header("🤖 AI Analysis")

    # ── Settings sidebar section ──
    with st.sidebar:
        st.divider()
        st.markdown("**AI Settings**")
        ai_provider = st.selectbox("Provider", ["Claude (Anthropic)", "ChatGPT (OpenAI)"], key="ai_provider")
        
        if ai_provider == "Claude (Anthropic)":
            api_key = st.text_input("Anthropic API Key", type="password", key="anthropic_key",
                                    help="Get your key at console.anthropic.com")
            model_options = ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5-20251001"]
            model = st.selectbox("Model", model_options, key="claude_model")
        else:
            api_key = st.text_input("OpenAI API Key", type="password", key="openai_key",
                                    help="Get your key at platform.openai.com")
            model_options = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"]
            model = st.selectbox("Model", model_options, key="openai_model")

    # ── Build context ──
    stats = get_trade_stats()
    all_journals = get_journal_entries()
    playbooks = get_playbooks()
    
    pb_stats_list = []
    for pb in playbooks:
        pbs = get_playbook_compliance_stats(pb["id"])
        if pbs:
            pbs["playbook_name"] = pb["name"]
            pb_stats_list.append(pbs)

    stats_context   = build_stats_context(stats, pb_stats_list)
    journal_context = build_journal_context(all_journals, limit=15)
    full_context    = stats_context + "\n\n" + journal_context

    # ── Tabs ──
    tab_quick, tab_chat, tab_prompts = st.tabs(["⚡ Quick Analysis", "💬 Chat", "📝 Saved Prompts"])

    with tab_quick:
        _quick_analysis(api_key, model, ai_provider, full_context)

    with tab_chat:
        _chat_interface(api_key, model, ai_provider, full_context)

    with tab_prompts:
        _saved_prompts(api_key, model, ai_provider, full_context)


def _quick_analysis(api_key, model, provider, context):
    st.subheader("Quick Analysis")
    st.caption("One-click deep dives into your trading data.")

    if not api_key:
        st.warning("⚠️ Enter your API key in the sidebar to use AI analysis.")
        _show_context_preview(context)
        return

    QUICK_PROMPTS = {
        "📊 Overall Performance Review": 
            "Analyse my overall trading performance. Identify my 3 biggest strengths and 3 biggest weaknesses. Be specific with numbers.",
        
        "🧠 Psychology & Discipline":
            "Based on my journal entries and stats, identify any psychological patterns affecting my trading. Look for revenge trading, fear, overconfidence, FOMO, or discipline issues.",
        
        "📖 Playbook Compliance":
            "Analyse my playbook rule compliance. Which rules am I consistently following or skipping? Is there a correlation between rule compliance and profitability?",
        
        "📉 Loss Analysis":
            "Deep dive into my losing trades and periods. What are the common characteristics? When do I lose most? Are there patterns in timing, symbols, or setups?",
        
        "🎯 Best Setup Identification":
            "Based on my statistics and journal, which setups, symbols, and conditions produce my best results? What should I focus on?",
        
        "🔄 Weekly Improvement Plan":
            "Based on everything you can see, write me a specific improvement plan for next week with 3 concrete actions I should take.",
        
        "⚠️ Risk Management Review":
            "Review my risk management. Am I consistent? Are there signs of position sizing issues, overleveraging, or poor stop placement? What should I change?",
    }

    col1, col2 = st.columns([2, 1])
    with col1:
        selected_prompt = st.selectbox("Select analysis type", list(QUICK_PROMPTS.keys()))
    with col2:
        st.write("")
        st.write("")
        run = st.button("🚀 Run Analysis", type="primary", use_container_width=True)

    if run:
        prompt = f"{QUICK_PROMPTS[selected_prompt]}\n\nHere is my trading data:\n\n{context}"
        with st.spinner(f"Analysing with {provider.split('(')[0].strip()}..."):
            try:
                if "Claude" in provider:
                    response = call_claude(api_key, prompt, model)
                else:
                    response = call_openai(api_key, prompt, model)
                
                st.markdown("---")
                st.markdown(response)

                # Save to session for reference
                if "quick_analyses" not in st.session_state:
                    st.session_state["quick_analyses"] = []
                st.session_state["quick_analyses"].insert(0, {
                    "type": selected_prompt,
                    "response": response,
                    "provider": provider,
                    "model": model,
                })
            except Exception as e:
                st.error(f"API error: {e}")
                if "api_key" in str(e).lower() or "auth" in str(e).lower():
                    st.info("Check that your API key is correct and has sufficient credits.")

    # Show past quick analyses
    past = st.session_state.get("quick_analyses", [])
    if len(past) > 1:
        with st.expander(f"📚 Past Analyses ({len(past)-1} previous)"):
            for a in past[1:6]:
                with st.expander(f"{a['type']} — {a['model']}"):
                    st.markdown(a["response"])


def _chat_interface(api_key, model, provider, context):
    st.subheader("Chat with your Trading Coach")
    st.caption("Ask anything about your trading. The AI has full context of your stats and journal.")

    if not api_key:
        st.warning("⚠️ Enter your API key in the sidebar.")
        return

    # Display chat history
    history = get_chat_history()

    chat_container = st.container()
    with chat_container:
        if not history:
            st.markdown("""
            <div style="text-align:center; padding: 30px; color: #6b7a99;">
                <div style="font-size: 2rem;">💬</div>
                <div>Start a conversation. I have full context of your trading stats and journal.</div>
                <div style="font-size: 0.85rem; margin-top: 8px;">Try: <em>"What's my biggest weakness right now?"</em></div>
            </div>
            """, unsafe_allow_html=True)
        else:
            for msg in history:
                if msg["role"] == "user":
                    st.markdown(f"""
                    <div style="display:flex; justify-content:flex-end; margin: 8px 0;">
                        <div style="background:#1e2a3a; border:1px solid #2a3a55; border-radius:12px 12px 2px 12px; padding:10px 14px; max-width:80%; color:#e8eaf0;">
                            {msg['content']}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div style="display:flex; justify-content:flex-start; margin: 8px 0;">
                        <div style="background:#131720; border:1px solid #00c896; border-left: 3px solid #00c896; border-radius:2px 12px 12px 12px; padding:10px 14px; max-width:85%; color:#e8eaf0;">
                            {msg['content']}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

    # Input
    col1, col2 = st.columns([5, 1])
    with col1:
        user_input = st.text_input(
            "Message",
            placeholder="Ask about your trading...",
            label_visibility="collapsed",
            key="chat_input"
        )
    with col2:
        send = st.button("Send ➤", type="primary", use_container_width=True)

    col3, col4 = st.columns([5, 1])
    with col4:
        if st.button("Clear chat", use_container_width=True):
            st.session_state["ai_chat_history"] = []
            st.rerun()

    if send and user_input:
        add_to_history("user", user_input)
        history = get_chat_history()

        with st.spinner("Thinking..."):
            try:
                # Build message list: inject context with first message
                if len(history) == 1:
                    # First message — include full context
                    messages = [{
                        "role": "user",
                        "content": f"Here is my complete trading data for context:\n\n{context}\n\n---\nMy question: {user_input}"
                    }]
                else:
                    # Ongoing conversation — use history directly
                    # First turn already had context, subsequent turns are plain
                    messages = []
                    for i, msg in enumerate(history):
                        if i == 0:
                            messages.append({
                                "role": "user",
                                "content": f"[Trading data context was provided at session start]\n\n{msg['content']}"
                            })
                        else:
                            messages.append(msg)

                if "Claude" in provider:
                    response = call_claude_chat(api_key, messages, model)
                else:
                    response = call_openai_chat(api_key, messages, model)

                add_to_history("assistant", response)
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
                # Remove the user message we just added since it failed
                st.session_state["ai_chat_history"].pop()


def _saved_prompts(api_key, model, provider, context):
    st.subheader("Prompt Library")
    st.caption("Custom prompts you can save and reuse.")

    if "custom_prompts" not in st.session_state:
        st.session_state["custom_prompts"] = [
            {
                "name": "Pre-session Briefing",
                "prompt": "Based on my recent performance and journal, give me a brief pre-session briefing. What should I focus on today? What mistakes should I avoid? Keep it to 5 bullet points."
            },
            {
                "name": "Monthly Review",
                "prompt": "Give me a thorough monthly performance review. Cover: what worked, what didn't, psychological themes, rule compliance, and 3 specific goals for next month."
            },
            {
                "name": "Identify Revenge Trading",
                "prompt": "Look through my journal and stats for signs of revenge trading. Do I have a pattern of taking impulsive trades after losses? Give me specific evidence and how to break the pattern."
            },
        ]

    prompts = st.session_state["custom_prompts"]

    for i, p in enumerate(prompts):
        with st.expander(f"📝 {p['name']}"):
            col1, col2, col3 = st.columns([4, 1, 1])
            with col1:
                edited_prompt = st.text_area("Prompt", value=p["prompt"], height=100, key=f"prompt_text_{i}")
            with col2:
                if api_key and st.button("▶ Run", key=f"run_prompt_{i}", type="primary"):
                    full_prompt = f"{edited_prompt}\n\nHere is my trading data:\n\n{context}"
                    with st.spinner("Running..."):
                        try:
                            if "Claude" in provider:
                                response = call_claude(api_key, full_prompt, model)
                            else:
                                response = call_openai(api_key, full_prompt, model)
                            st.session_state[f"prompt_result_{i}"] = response
                        except Exception as e:
                            st.error(str(e))
            with col3:
                if st.button("🗑️ Del", key=f"del_prompt_{i}"):
                    st.session_state["custom_prompts"].pop(i)
                    st.rerun()

            result = st.session_state.get(f"prompt_result_{i}")
            if result:
                st.markdown("---")
                st.markdown(result)

    st.divider()
    st.subheader("Add New Prompt")
    with st.form("new_custom_prompt"):
        new_name   = st.text_input("Prompt Name")
        new_prompt = st.text_area("Prompt Text", height=120,
                                   placeholder="Write your custom analysis prompt here...")
        if st.form_submit_button("Save Prompt", type="primary"):
            if new_name and new_prompt:
                st.session_state["custom_prompts"].append({"name": new_name, "prompt": new_prompt})
                st.success("Prompt saved!")
                st.rerun()


def _show_context_preview(context):
    """Show what data would be sent to the AI."""
    with st.expander("👁 Preview data that will be sent to AI"):
        st.caption("This is the trading context that gets sent with every AI request.")
        st.text(context[:3000] + ("..." if len(context) > 3000 else ""))
