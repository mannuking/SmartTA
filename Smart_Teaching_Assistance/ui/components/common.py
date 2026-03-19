import streamlit as st
from pathlib import Path
from Smart_Teaching_Assistance.ui.state_manager import STEPS, STEP_ICONS, go

def load_css():
    # Correct path: style.css is in ui/, and this file is in ui/components/
    css = Path(__file__).parent.parent / "style.css"
    if css.exists():
        st.markdown(f"<style>{css.read_text()}</style>", unsafe_allow_html=True)
    else:
        # Emergency fallback for dev/rel paths
        root_css = Path("Smart_Teaching_Assistance/ui/style.css")
        if root_css.exists():
             st.markdown(f"<style>{root_css.read_text()}</style>", unsafe_allow_html=True)

def render_progress():
    cur = st.session_state.step
    if cur == -1: return
    
    done = [False] * len(STEPS)
    done[0] = bool(st.session_state.syllabus_text)
    done[1] = st.session_state.roadmap_generated
    done[2] = st.session_state.lesson_plan_generated
    done[3] = st.session_state.detailed_notes_generated
    done[4] = len(st.session_state.personalized_versions) > 0

    html_parts = []
    for i, (label, icon) in enumerate(zip(STEPS, STEP_ICONS)):
        if i == cur:
            cls = "active"
        elif done[i]:
            cls = "done"
        else:
            cls = "pending"
        dot = f'<span class="step-dot {cls}" title="{label}">{icon}</span>'
        html_parts.append(dot)
        if i < len(STEPS) - 1:
            line_cls = "done" if done[i] else ""
            html_parts.append(f'<span class="step-line {line_cls}"></span>')

    st.markdown(
        f'<div class="step-progress">{"".join(html_parts)}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(f"### {STEP_ICONS[cur]}  Step {cur+1} / {len(STEPS)} — {STEPS[cur]}")

def nav_buttons(back=True, next_=True, next_label="Next Step →", next_disabled=False,
                back_step=None, next_step=None):
    cur = st.session_state.step
    b_step = back_step if back_step is not None else max(0, cur - 1)
    n_step = next_step if next_step is not None else min(len(STEPS) - 1, cur + 1)

    cols = st.columns([1, 4, 1])
    with cols[0]:
        if back and cur > 0:
            st.button("← Back", use_container_width=True,
                      on_click=go, args=(b_step,), key=f"nav_back_{cur}")
    with cols[2]:
        if next_ and cur < len(STEPS) - 1:
            st.button(next_label, type="primary", use_container_width=True,
                      disabled=next_disabled,
                      on_click=go, args=(n_step,), key=f"nav_next_{cur}")

def count_tree(items):
    c = 0
    for item in items:
        c += 1
        for key in ("subtopics", "subsubtopics", "subsubsubtopics"):
            if key in item and item[key]:
                c += count_tree(item[key])
    return c
