import streamlit as st
from werkzeug.security import generate_password_hash, check_password_hash
from Smart_Teaching_Assistance.ui.state_manager import get_db, log_activity, create_auth_session, clear_auth_session

def render_auth():
    """Render a clean login/signup interface."""
    st.markdown("""
        <div style="text-align: center; margin-bottom: 2rem;">
            <h1 style="font-size: 2.5rem; background: linear-gradient(90deg, #6366F1, #38BDF8); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
                Smart Teaching Assistant
            </h1>
            <p style="color: #64748b; font-size: 1.1rem;">Your AI-Powered Pedagogy Partner</p>
        </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        tabs = st.tabs(["🔐 Login", "📝 Sign Up"])
        
        with tabs[0]:
            with st.form("login_form"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                submit = st.form_submit_button("Login", use_container_width=True)
                
                if submit:
                    if _login(username, password):
                        st.rerun()
                    else:
                        st.error("Invalid username or password.")
        
        with tabs[1]:
            with st.form("signup_form"):
                new_username = st.text_input("Choose Username")
                new_password = st.text_input("Choose Password", type="password")
                confirm_password = st.text_input("Confirm Password", type="password")
                signup_submit = st.form_submit_button("Create Account", use_container_width=True)
                
                if signup_submit:
                    if not new_username or not new_password:
                        st.error("Username and password are required.")
                    elif new_password != confirm_password:
                        st.error("Passwords do not match.")
                    else:
                        if _signup(new_username, new_password):
                            st.success("Account created! You can now log in.")
                        else:
                            st.error("Username already exists.")

def _login(username, password):
    db = get_db()
    row = db.fetchone("SELECT id, username, password_hash FROM users WHERE username = ?", (username,))
    if row and check_password_hash(row["password_hash"], password):
        st.session_state.user_id = row["id"]
        st.session_state.username = row["username"]
        create_auth_session(row["id"], row["username"])
        log_activity(f"User {username} logged in", icon="🔓")
        return True
    return False

def _signup(username, password):
    db = get_db()
    hashed = generate_password_hash(password)
    try:
        db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, hashed))
        log_activity(f"New user signed up: {username}", icon="👤")
        return True
    except Exception:
        return False

def logout():
    """Clear session and log out."""
    from Smart_Teaching_Assistance.ui.state_manager import reset_session
    clear_auth_session()
    reset_session()
    st.session_state.user_id = None
    st.session_state.username = None
    st.rerun()
