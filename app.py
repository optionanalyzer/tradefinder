import streamlit as st
import pandas as pd
import requests
import urllib.parse
from datetime import datetime
import pytz
from streamlit_autorefresh import st_autorefresh
import plotly.graph_objects as go
import sqlite3
import uuid
import hashlib

# ===================================================================
# ⚠️ HARDCODE YOUR ACCESS TOKEN HERE
# ===================================================================
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiIzTUJDMzIiLCJqdGkiOiI2YTZhYjk0ODhhMjRiODY1ZWQyMzUzY2EiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NTM3OTE0NCwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzg1NDQ4ODAwfQ.j1Vsy-7HrSvXw3QN-oOL3aOHbx0eXEjmtedV2-B18uQ" 

# ===================================================================
# 0. DATABASE & SINGLE-SESSION AUTHENTICATION ENGINE
# ===================================================================
def init_db():
    """Initializes a local SQLite database for user management."""
    conn = sqlite3.connect('fno_users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (username TEXT PRIMARY KEY, password TEXT, active_session TEXT)''')
    
    # Create a default user (Username: admin | Password: admin123)
    default_pw = hashlib.sha256('admin123'.encode()).hexdigest()
    c.execute("INSERT OR IGNORE INTO users (username, password, active_session) VALUES (?, ?, ?)", 
              ('admin', default_pw, None))
    conn.commit()
    conn.close()

init_db()

# Initialize local session variables
if 'session_id' not in st.session_state:
    st.session_state.session_id = None
if 'username' not in st.session_state:
    st.session_state.username = None

def verify_session():
    """Checks if the current browser tab holds the active DB lock."""
    if not st.session_state.session_id or not st.session_state.username:
        return False
    
    conn = sqlite3.connect('fno_users.db')
    c = conn.cursor()
    c.execute("SELECT active_session FROM users WHERE username=?", (st.session_state.username,))
    row = c.fetchone()
    conn.close()
    
    # If the DB session matches our local session, we are authorized
    if row and row[0] == st.session_state.session_id:
        return True
    return False

def logout():
    """Clears the DB lock and local session."""
    if st.session_state.username:
        conn = sqlite3.connect('fno_users.db')
        c = conn.cursor()
        c.execute("UPDATE users SET active_session = NULL WHERE username=?", (st.session_state.username,))
        conn.commit()
        conn.close()
    st.session_state.session_id = None
    st.session_state.username = None
    st.rerun()

# --- THE LOGIN SCREEN ---
if not verify_session():
    # We must call page config here if not authenticated, as it must be the first Streamlit command
    st.set_page_config(page_title="Login - FnO Terminal", layout="centered")
    
    st.markdown("<h2 style='text-align: center;'>🔐 FnO Intelligence Terminal</h2>", unsafe_allow_html=True)
    st.markdown("---")
    
    with st.form("login_form"):
        st.write("Please log in to access the dashboard.")
        input_user = st.text_input("Username")
        input_pass = st.text_input("Password", type="password")
        force_login = st.checkbox("Force terminate other active sessions")
        
        submit = st.form_submit_button("Login to Terminal")
        
        if submit:
            hashed_pass = hashlib.sha256(input_pass.encode()).hexdigest()
            conn = sqlite3.connect('fno_users.db')
            c = conn.cursor()
            c.execute("SELECT password, active_session FROM users WHERE username=?", (input_user,))
            user_data = c.fetchone()
            
            if user_data:
                db_pass, db_session = user_data
                if db_pass == hashed_pass:
                    if db_session is not None and not force_login:
                        st.error("⚠️ You are already logged in on another device/browser. Check the box above to terminate it.")
                    else:
                        # Grant access and lock the session
                        new_session_id = str(uuid.uuid4())
                        c.execute("UPDATE users SET active_session=? WHERE username=?", (new_session_id, input_user))
                        conn.commit()
                        st.session_state.session_id = new_session_id
                        st.session_state.username = input_user
                        st.success("Login successful! Redirecting...")
                        st.rerun()
                else:
                    st.error("Invalid password.")
            else:
                st.error("Invalid username.")
            conn.close()
            
    # Stop the rest of the app from running if not logged in
    st.stop()

# ===================================================================
# END AUTHENTICATION - MAIN APP CONTINUES BELOW
# ===================================================================

# -------------------------------------------------------------------
# 0. PAGE CONFIGURATION & AUTO REFRESH
# -------------------------------------------------------------------
st.set_page_config(page_title="FnO Intelligence Terminal", layout="wide")

# Run a seamless background refresh every 15 seconds
st_autorefresh(interval=15000, limit=None, key="fno_terminal_refresh")

# -------------------------------------------------------------------
# 1. FETCH AND PROCESS UPSTOX INSTRUMENT DATA
# -------------------------------------------------------------------
@st.cache_data(show_spinner="Fetching Master Instrument List from Upstox...")
def load_instruments():
    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
    df = pd.read_csv(url)
    
    # Capturing both NSE and BSE elements to include SENSEX/BANKEX
    fno_df = df[
        df['instrument_key'].str.startswith('NSE_FO|') | 
        df['instrument_key'].str.startswith('NSE_INDEX|') |
        df['instrument_key'].str.startswith('BSE_FO|') |
        df['instrument_key'].str.startswith('BSE_INDEX|')
    ]
    
    unique_symbols = fno_df['name'].dropna().unique().tolist()
    unique_symbols.sort()
    
    return df, fno_df, unique_symbols

master_df, fno_df, fno_symbols = load_instruments()

# -------------------------------------------------------------------
# 2. CORE API DATA FETCHERS
# -------------------------------------------------------------------
def get_expiries_for_symbol(symbol, df):
    symbol_data = df[df['name'] == symbol]
    expiries = symbol_data['expiry'].dropna().unique().tolist()
    return sorted(expiries)

def get_underlying_ltp(instrument_key, access_token):
    safe_key = urllib.parse.quote(instrument_key)
    url = f"https://api.upstox.com/v3/market-quote/ltp?instrument_key={safe_key}"
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {access_token}'}
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json().get('data', {})
            if data:
                return list(data.values())[0].get('last_price')
    except Exception:
        pass
    return None

def fetch_live_vix(access_token):
    if not access_token or access_token == "YOUR_UPSTOX_ACCESS_TOKEN_HERE":
        return "N/A"
        
    safe_key = urllib.parse.quote("NSE_INDEX|India VIX")
    url = f"https://api.upstox.com/v3/market-quote/ltp?instrument_key={safe_key}"
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {access_token}'}
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json().get('data', {})
            if data:
                ltp = list(data.values())[0].get('last_price')
                if ltp is not None:
                    return str(ltp)
        return f"Err: {response.status_code}"
    except Exception:
        return "N/A"

def get_option_chain_data(instrument_key, expiry_date, access_token, spot_price):
    if not access_token or access_token == "YOUR_UPSTOX_ACCESS_TOKEN_HERE" or not spot_price:
        return None, None

    safe_key = urllib.parse.quote(instrument_key)
    url = f"https://api.upstox.com/v2/option/chain?instrument_key={safe_key}&expiry_date={expiry_date}"
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {access_token}'}

    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return None, None
        
        data = response.json().get('data', [])
        if not data:
            return None, None

        chain_df = pd.json_normalize(data)
        chain_df.fillna(0, inplace=True)
        
        chain_df['distance_from_spot'] = abs(chain_df['strike_price'] - spot_price)
        atm_index = chain_df['distance_from_spot'].idxmin()
        atm_strike = chain_df.loc[atm_index, 'strike_price']

        return chain_df, atm_strike
    except Exception:
        return None, None

# -------------------------------------------------------------------
# 3. TERMINAL HEADER USER INTERFACE
# -------------------------------------------------------------------
st.markdown("### 📊 FnO Intelligence Terminal")
st.markdown("---")

col1, col2, col3, col4, col5 = st.columns([1.5, 1.5, 1, 1, 0.5])

with col1:
    selected_symbol = st.selectbox(
        "Select Instrument", 
        options=fno_symbols,
        index=None,
        placeholder="Search for an instrument...",
        label_visibility="collapsed"
    )

with col2:
    available_expiries = get_expiries_for_symbol(selected_symbol, fno_df) if selected_symbol else []
    selected_expiry = st.selectbox(
        "Select Expiry", 
        options=available_expiries if available_expiries else ["No Expiry Found"],
        label_visibility="collapsed"
    )

INDEX_MAP = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
    "SENSEX": "BSE_INDEX|SENSEX",
    "BANKEX": "BSE_INDEX|BANKEX"
}

if selected_symbol in INDEX_MAP:
    target_instrument_key = INDEX_MAP[selected_symbol]
elif selected_symbol:
    eq_rows = master_df[(master_df['name'] == selected_symbol) & (master_df['instrument_key'].str.startswith('NSE_EQ|'))]
    target_instrument_key = eq_rows['instrument_key'].iloc[0] if not eq_rows.empty else ""
else:
    target_instrument_key = ""

# -------------------------------------------------------------------
# 4. EXECUTE DATA STREAMS
# -------------------------------------------------------------------
live_vix = fetch_live_vix(ACCESS_TOKEN)
underlying_spot = get_underlying_ltp(target_instrument_key, ACCESS_TOKEN) if target_instrument_key else None

chain_df, atm_strike = None, None
if available_expiries and target_instrument_key:
    chain_df, atm_strike = get_option_chain_data(target_instrument_key, selected_expiry, ACCESS_TOKEN, underlying_spot)

live_pcr = None
active_strikes_df = pd.DataFrame()
greeks_display_df = pd.DataFrame()

if chain_df is not None:
    atm_idx = chain_df['distance_from_spot'].idxmin()
    # Isolate ATM +/- 5 strikes for targeted calculations
    active_strikes_df = chain_df.iloc[max(0, atm_idx - 5):min(len(chain_df) - 1, atm_idx + 5) + 1].copy()
    
    total_call_oi = active_strikes_df.get('call_options.market_data.oi', pd.Series([0])).sum()
    total_put_oi = active_strikes_df.get('put_options.market_data.oi', pd.Series([0])).sum()
    
    live_pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 99.9

with col3:
    if live_pcr is not None:
        pcr_color = "normal" if live_pcr >= 1 else "inverse"
        st.metric(label="PCR (ATM±5 Total OI)", value=live_pcr, delta=f"ATM: {int(atm_strike)}", delta_color=pcr_color)
    else:
        st.metric(label="PCR (ATM±5 Total OI)", value="---", delta="No Data", delta_color="off")

with col4:
    st.metric(label="INDIA VIX", value=live_vix)

st.markdown("---")

with col5:
    if st.button("Logout 🚪", use_container_width=True):
        logout()


# -------------------------------------------------------------------
# 5. INTRADAY OI CHANGE TRACKER (Attached to Active Strikes)
# -------------------------------------------------------------------
if not active_strikes_df.empty:
    if 'oi_baselines' not in st.session_state:
        st.session_state.oi_baselines = {}
        
    state_prefix = f"{target_instrument_key}_{selected_expiry}"
    call_chg_list = []
    put_chg_list = []

    for index, row in active_strikes_df.iterrows():
        strike = row['strike_price']
        current_call_oi = row.get('call_options.market_data.oi', 0)
        current_put_oi = row.get('put_options.market_data.oi', 0)
        
        dict_key = f"{state_prefix}_{strike}"
        
        if dict_key not in st.session_state.oi_baselines:
            st.session_state.oi_baselines[dict_key] = {'call_oi': current_call_oi, 'put_oi': current_put_oi}
            
        baseline = st.session_state.oi_baselines[dict_key]
        call_chg_list.append(current_call_oi - baseline['call_oi'])
        put_chg_list.append(current_put_oi - baseline['put_oi'])
        
    # Attach data globally
    active_strikes_df['call_chg_oi'] = call_chg_list
    active_strikes_df['put_chg_oi'] = put_chg_list

# -------------------------------------------------------------------
# 6. VISUAL OI BUILDUP, S&R LEVELS & TREND CHARTS
# -------------------------------------------------------------------
if not active_strikes_df.empty:
    st.markdown("---")
    
    # --- RESCUED S&R CALCULATION ---
    # We must calculate this here since the Momentum Engine was removed
    try:
        res_idx = active_strikes_df['call_options.market_data.oi'].idxmax()
        resistance_strike = active_strikes_df.loc[res_idx, 'strike_price']
        
        sup_idx = active_strikes_df['put_options.market_data.oi'].idxmax()
        support_strike = active_strikes_df.loc[sup_idx, 'strike_price']
    except Exception:
        resistance_strike, support_strike = 0, 0

    color_call = 'rgba(255, 75, 75, 1)' 
    color_put = 'rgba(29, 201, 115, 1)' 

    # 1. OI Buildup and Change in OI Charts
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.markdown("<h5 style='text-align: center;'>POSITION BUILDUP</h5>", unsafe_allow_html=True)
        fig_oi = go.Figure()
        fig_oi.add_trace(go.Bar(
            x=active_strikes_df['strike_price'], 
            y=active_strikes_df.get('call_options.market_data.oi', pd.Series([0]*len(active_strikes_df))),
            name='CALL', marker_color=color_call
        ))
        fig_oi.add_trace(go.Bar(
            x=active_strikes_df['strike_price'], 
            y=active_strikes_df.get('put_options.market_data.oi', pd.Series([0]*len(active_strikes_df))),
            name='PUT', marker_color=color_put
        ))
        fig_oi.update_layout(
            barmode='group', margin=dict(l=0, r=0, t=30, b=0),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            xaxis=dict(type='category', tickangle=-45) 
        )
        st.plotly_chart(fig_oi, use_container_width=True, config={'displayModeBar': False}, key="chart_oi_buildup")

    with chart_col2:
        st.markdown("<h5 style='text-align: center;'>CHANGE IN POSITIONS (SESSION)</h5>", unsafe_allow_html=True)
        fig_chg = go.Figure()
        fig_chg.add_trace(go.Bar(
            x=active_strikes_df['strike_price'], 
            y=active_strikes_df['call_chg_oi'], 
            name='CALL', marker_color=color_call
        ))
        fig_chg.add_trace(go.Bar(
            x=active_strikes_df['strike_price'], 
            y=active_strikes_df['put_chg_oi'], 
            name='PUT', marker_color=color_put
        ))
        fig_chg.update_layout(
            barmode='group', margin=dict(l=0, r=0, t=30, b=0),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            xaxis=dict(type='category', tickangle=-45)
        )
        st.plotly_chart(fig_chg, use_container_width=True, config={'displayModeBar': False}, key="chart_oi_change")

    # 2. Support, Resistance, and Battleground Boxes
    st.write("") 
    box_col1, box_col2, box_col3 = st.columns(3)
    
    active_strikes_df['Total_Activity'] = active_strikes_df.get('call_options.market_data.oi', 0) + active_strikes_df.get('put_options.market_data.oi', 0)
    bg_idx = active_strikes_df['Total_Activity'].idxmax()
    battleground_strike = active_strikes_df.loc[bg_idx, 'strike_price'] if pd.notna(bg_idx) else 0

    with box_col1:
        st.markdown(f"""
        <div style="background-color:#1e1e1e; padding:15px; border-radius:10px; text-align:center;">
            <p style="color:#ff4b4b; margin:0; font-weight:bold; font-size:12px;">ACTIVE RES (CHG)</p>
            <h3 style="margin:0;">{int(resistance_strike) if resistance_strike else 0}</h3>
        </div>
        """, unsafe_allow_html=True)
        
    with box_col2:
        st.markdown(f"""
        <div style="background-color:#1e1e1e; padding:15px; border-radius:10px; text-align:center;">
            <p style="color:#1dc973; margin:0; font-weight:bold; font-size:12px;">ACTIVE SUP (CHG)</p>
            <h3 style="margin:0;">{int(support_strike) if support_strike else 0}</h3>
        </div>
        """, unsafe_allow_html=True)
        
    with box_col3:
        st.markdown(f"""
        <div style="background-color:#1e1e1e; padding:15px; border-radius:10px; text-align:center;">
            <p style="color:#faca2b; margin:0; font-weight:bold; font-size:12px;">BATTLEGROUND</p>
            <h3 style="margin:0;">{int(battleground_strike)}</h3>
        </div>
        """, unsafe_allow_html=True)

    # 3. Time-Series Logging for Trends
    if 'history_df' not in st.session_state:
        st.session_state.history_df = pd.DataFrame(columns=['Time_IST', 'PCR', 'VIX'])
    
    ist = pytz.timezone('Asia/Kolkata')
    current_time_str = datetime.now(ist).strftime('%H:%M:%S')
    
    # Check if variables exist before appending to avoid errors
    try:
        current_vix_val = float(live_vix)
    except Exception:
        current_vix_val = 15.0

    new_data = pd.DataFrame([{'Time_IST': current_time_str, 'PCR': live_pcr, 'VIX': current_vix_val}])
    st.session_state.history_df = pd.concat([st.session_state.history_df, new_data], ignore_index=True)
    st.session_state.history_df = st.session_state.history_df.tail(20)

    st.markdown("---")

    # 4. PCR and VIX Trend Charts
    row6_col1, row6_col2 = st.columns(2)

    with row6_col1:
        st.markdown("<h5 style='text-align: center;'>MARKET TREND</h5>", unsafe_allow_html=True)
        fig_pcr = go.Figure()
        fig_pcr.add_trace(go.Scatter(
            x=st.session_state.history_df['Time_IST'], y=st.session_state.history_df['PCR'],
            mode='lines+markers', line=dict(color='#a855f7', width=3) 
        ))
        fig_pcr.update_layout(
            margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(showgrid=True, gridcolor='#333'), yaxis=dict(showgrid=True, gridcolor='#333')
        )
        st.plotly_chart(fig_pcr, use_container_width=True, config={'displayModeBar': False}, key="chart_pcr_trend")

    with row6_col2:
        st.markdown("<h5 style='text-align: center;'>FEAR INDEX</h5>", unsafe_allow_html=True)
        fig_vix = go.Figure()
        fig_vix.add_trace(go.Scatter(
            x=st.session_state.history_df['Time_IST'], y=st.session_state.history_df['VIX'],
            mode='lines+markers', line=dict(color='#1dc973', width=3) 
        ))
        fig_vix.update_layout(
            margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(showgrid=True, gridcolor='#333'), yaxis=dict(showgrid=True, gridcolor='#333')
        )
        st.plotly_chart(fig_vix, use_container_width=True, config={'displayModeBar': False}, key="chart_vix_trend")

