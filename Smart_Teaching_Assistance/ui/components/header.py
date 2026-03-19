import streamlit as st
from Smart_Teaching_Assistance.ui.state_manager import (
    has_api_key, get_all_api_keys, add_api_key, remove_api_key,
)

def render_api_config():
    """Render a standalone API key configuration block with expander logic."""
    has_keys = has_api_key()
    all_keys = get_all_api_keys()
    active_key = st.session_state.get("gemini_api_key", "")

    label = "✅ API Configured" if has_keys else "🔑 Configure API Key"
    
    # We use a container with a button or a styled expander
    with st.expander(label, expanded=not has_keys):
        st.markdown(
            '<div style="font-size:0.95rem;margin-bottom:12px;color:#64748b">'
            'Manage your Gemini API keys for automatic rotation and rate-limit recovery.</div>',
            unsafe_allow_html=True
        )
        
        # List existing keys
        if all_keys:
            st.markdown('<div style="font-weight:600;font-size:0.9rem;margin:8px 0 6px 0">Active Keys</div>', unsafe_allow_html=True)
            for k in all_keys:
                is_active = (k == active_key)
                disp_key = f"{k[:12]}...{k[-8:]}"
                
                k_col1, k_col2 = st.columns([5, 1])
                with k_col1:
                    bg = "#EEF2FF" if is_active else "#f8fafc"
                    border = "#6366F1" if is_active else "#e2e8f0"
                    status = " (In Use)" if is_active else ""
                    st.markdown(
                        f'<div style="background:{bg};padding:8px 12px;border-radius:8px;'
                        f'border:1px solid {border};font-family:monospace;font-size:0.85rem">'
                        f'<code>{disp_key}</code>{status}</div>',
                        unsafe_allow_html=True
                    )
                with k_col2:
                    if st.button("🗑️", key=f"hdr_rm_{k[:10]}", help="Remove this key"):
                        remove_api_key(k)
                        st.rerun()
        
        st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)
        
        # Add new key (form supports Enter submit and avoids ambiguous click state)
        with st.form("hdr_add_key_form", clear_on_submit=True):
            new_key = st.text_input(
                "Add New Gemini API Key",
                type="password",
                key="hdr_add_new_key",
                placeholder="AIza...",
                help="Get a free key at https://aistudio.google.com/apikey",
            )
            add_submit = st.form_submit_button("➕ Add Key", use_container_width=True)

        if add_submit:
            added = add_api_key(new_key)
            if added:
                st.toast("API key added", icon="✅")
                st.rerun()
            elif not new_key.strip():
                st.warning("Enter an API key first.")
            elif new_key.strip() in st.session_state.get("gemini_api_keys", []):
                st.info("That API key already exists.")
            else:
                st.error("Invalid API key format. Please check and try again.")

    if not has_keys:
        st.info("You must configure at least one API key to unlock the workflow chapters.", icon="ℹ️")
