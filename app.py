import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import os
import requests
from icalendar import Calendar
from datetime import datetime, timedelta, date
import urllib.parse
import hashlib
from zoneinfo import ZoneInfo
import streamlit.components.v1 as components

# --- 1. ΛΕΙΤΟΥΡΓΙΑ ΓΙΑ ΑΥΤΟΜΑΤΟ ΚΛΕΙΣΙΜΟ SIDEBAR ---
def auto_collapse_sidebar():
    components.html(
        """
        <script>
        var button = window.parent.document.querySelector('button[aria-label="Close sidebar"]') || 
                     window.parent.document.querySelector('button[data-testid="sidebar-close-button"]') ||
                     window.parent.document.querySelector('button[kind="headerNoPadding"]');
        if (button) {
            setTimeout(function() { button.click(); }, 100);
        }
        </script>
        """,
        height=0,
    )

# --- Φάκελος για Uploads ---
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- ΣΥΝΔΕΣΗ ΜΕ GOOGLE SHEETS ---
def get_gsheet_client():
    try:
        creds_dict = st.secrets["gcp_service_account"]
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        # Προσπάθεια ανοίγματος του spreadsheet - βεβαιωθείτε ότι το όνομα είναι σωστό
        return client.open("mylessons")
    except Exception as e:
        st.error(f"Σφάλμα σύνδεσης με Google Sheets: {e}")
        return None

# --- ΣΥΝΑΡΤΗΣΕΙΣ ΑΣΦΑΛΕΙΑΣ & ΔΙΑΧΕΙΡΙΣΗΣ ΧΡΗΣΤΩΝ ---
def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def get_users():
    sheet = get_gsheet_client()
    if not sheet: 
        return pd.DataFrame(columns=["username", "password", "cal_url"])
    try:
        ws = sheet.worksheet("users")
        data = ws.get_all_records()
        return pd.DataFrame(data) if data else pd.DataFrame(columns=["username", "password", "cal_url"])
    except:
        return pd.DataFrame(columns=["username", "password", "cal_url"])

def save_user(username, password, cal_url):
    sheet = get_gsheet_client()
    if not sheet: return False
    ws = sheet.worksheet("users")
    df = get_users()
    if username in df['username'].values:
        return False
    ws.append_row([username, hash_pw(password), cal_url])
    return True

def update_user_data(username, new_url, new_pw=None):
    sheet = get_gsheet_client()
    if not sheet: return False
    ws = sheet.worksheet("users")
    df = get_users()
    if username in df['username'].values:
        idx = df[df['username'] == username].index[0] + 2
        ws.update_cell(idx, 3, new_url)
        if new_pw:
            ws.update_cell(idx, 2, hash_pw(new_pw))
        return True
    return False

def delete_user_account(username):
    sheet = get_gsheet_client()
    if not sheet: return
    ws_u = sheet.worksheet("users")
    users = pd.DataFrame(ws_u.get_all_records())
    if username in users['username'].values:
        idx = users[users['username'] == username].index[0] + 2
        ws_u.delete_rows(idx)
    
    for tab in ["students", "lessons", "notes"]:
        try:
            ws = sheet.worksheet(tab)
            data = pd.DataFrame(ws.get_all_records())
            if not data.empty and 'owner' in data.columns:
                filtered = data[data['owner'] != username]
                ws.clear()
                ws.update([filtered.columns.values.tolist()] + filtered.values.tolist())
        except: pass

# --- ΦΟΡΤΩΣΗ & ΑΠΟΘΗΚΕΥΣΗ ΜΕ ΔΙΟΡΘΩΣΗ ΔΕΚΑΔΙΚΩΝ & ΤΗΛΕΦΩΝΩΝ ---
@st.cache_data(ttl=600)
def load_data_from_sheet(tab_name, username):
    sheet = get_gsheet_client()
    if not sheet: return pd.DataFrame()
    try:
        ws = sheet.worksheet(tab_name)
        df_all = pd.DataFrame(ws.get_all_records())
        if not df_all.empty and 'owner' in df_all.columns:
            df_filtered = df_all[df_all['owner'] == username].drop(columns=['owner']).reset_index(drop=True)
            
            # ΔΙΟΡΘΩΣΗ ΤΗΛΕΦΩΝΟΥ (Αφαίρεση .0)
            if 'Τηλέφωνο' in df_filtered.columns:
                df_filtered['Τηλέφωνο'] = df_filtered['Τηλέφωνο'].astype(str).replace(r'\.0$', '', regex=True)
            
            # ΔΙΟΡΘΩΣΗ ΠΟΣΩΝ (Μετατροπή σε float)
            if 'Ποσό' in df_filtered.columns:
                df_filtered['Ποσό'] = pd.to_numeric(df_filtered['Ποσό'].astype(str).str.replace(',', '.'), errors='coerce').fillna(0.0)
            if 'Τιμή' in df_filtered.columns:
                df_filtered['Τιμή'] = pd.to_numeric(df_filtered['Τιμή'].astype(str).str.replace(',', '.'), errors='coerce').fillna(0.0)
                
            return df_filtered
        return pd.DataFrame()
    except: return pd.DataFrame()

def save_data_to_sheet(df, tab_name, username):
    sheet = get_gsheet_client()
    if not sheet: return
    try:
        ws = sheet.worksheet(tab_name)
        all_data = pd.DataFrame(ws.get_all_records())
        others = all_data[all_data['owner'] != username] if not all_data.empty and 'owner' in all_data.columns else pd.DataFrame()
        
        mine = df.copy()
        mine.insert(0, 'owner', username)
        
        # Καθαρισμός τηλεφώνων πριν την αποθήκευση
        if 'Τηλέφωνο' in mine.columns:
            mine['Τηλέφωνο'] = mine['Τηλέφωνο'].astype(str).replace(r'\.0$', '', regex=True)

        final_df = pd.concat([others, mine], ignore_index=True).fillna("")
        ws.clear()
        ws.update([final_df.columns.values.tolist()] + final_df.values.tolist(), value_input_option='USER_ENTERED')
    except: pass

def load_data(username):
    if 'last_load' in st.session_state:
        if (datetime.now() - st.session_state.last_load).total_seconds() < 5:
            return
            
    st.session_state.df_s = load_data_from_sheet("students", username)
    if st.session_state.df_s.empty: 
        st.session_state.df_s = pd.DataFrame(columns=["Όνομα", "Τηλέφωνο", "Τιμή"])
    
    st.session_state.df_l = load_data_from_sheet("lessons", username)
    if st.session_state.df_l.empty:
        st.session_state.df_l = pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Ώρα", "Λήξη", "Ποσό", "Κατάσταση", "Πληρώθηκε", "UID"])

    st.session_state.df_n = load_data_from_sheet("notes", username)
    if st.session_state.df_n.empty:
        st.session_state.df_n = pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Σημειώσεις", "Αρχείο", "Διαγωνίσματα"])
    else:
        today_str = datetime.now(ZoneInfo('Europe/Athens')).strftime('%Y-%m-%d')
        st.session_state.df_n['Διαγωνίσματα'] = st.session_state.df_n['Διαγωνίσματα'].apply(lambda x: x if str(x) >= today_str else "")

    st.session_state.last_load = datetime.now()

def save_all():
    user = st.session_state.user
    save_data_to_sheet(st.session_state.df_s, "students", user)
    save_data_to_sheet(st.session_state.df_l, "lessons", user)
    save_data_to_sheet(st.session_state.df_n, "notes", user)
    st.cache_data.clear()

def auto_sync():
    cal_url = st.session_state.cal_url
    if not cal_url or str(cal_url) == "nan": return
    try:
        res = requests.get(cal_url, timeout=5)
        gcal = Calendar.from_ical(res.content)
        gr_tz = ZoneInfo('Europe/Athens')
        now = datetime.now(gr_tz).replace(tzinfo=None)

        # Αφαιρούμε τα παλιά αυτόματα προγραμματισμένα
        st.session_state.df_l = st.session_state.df_l[
            (st.session_state.df_l['Κατάσταση'] != "Προγραμματισμένο") | 
            (st.session_state.df_l['UID'].astype(str).str.startswith('manual_'))
        ].reset_index(drop=True)

        start_limit, end_limit = now - timedelta(days=7), now + timedelta(days=30)
        new_lessons = []
        for comp in gcal.walk('VEVENT'):
            summary = str(comp.get('summary', ''))
            uid = str(comp.get('uid', ''))
            if not summary.strip().lower().startswith("μάθημα"): continue
            
            start = comp.get('dtstart').dt
            if not isinstance(start, datetime): continue
            start = start.astimezone(gr_tz).replace(tzinfo=None)
            end = comp.get('dtend').dt if comp.get('dtend') else start + timedelta(hours=1)
            if isinstance(end, datetime): end = end.astimezone(gr_tz).replace(tzinfo=None)

            if start_limit <= start <= end_limit:
                match = next((s for _, s in st.session_state.df_s.iterrows() if s['Όνομα'].lower() in summary.lower()), None)
                if match is not None:
                    d_str, t_start, t_end = start.strftime('%d/%m/%Y'), start.strftime('%H:%M'), end.strftime('%H:%M')
                    hourly_rate = float(str(match['Τιμή']).replace(',', '.'))
                    duration = (end - start).total_seconds() / 3600
                    price = round(duration * hourly_rate, 2)
                    
                    status = "Προγραμματισμένο" if now < end else "Ολοκληρώθηκε"
                    if status == "Ολοκληρώθηκε" and not st.session_state.df_l[st.session_state.df_l['UID'] == uid].empty:
                        continue
                        
                    new_lessons.append([match['Όνομα'], d_str, t_start, t_end, price, status, "Όχι", uid])
        
        if new_lessons:
            new_df = pd.DataFrame(new_lessons, columns=st.session_state.df_l.columns)
            st.session_state.df_l = pd.concat([st.session_state.df_l, new_df], ignore_index=True)
            save_all()
    except: pass

# --- UI SECTIONS ---
def show_dashboard():
    st.header("📊 Dashboard")
    col_m1, col_m2, col_m3 = st.columns(3)
    gr_tz = ZoneInfo('Europe/Athens')
    today = datetime.now(gr_tz).strftime('%d/%m/%Y')
    
    # Διασφάλιση αριθμητικών δεδομένων για τα metrics
    st.session_state.df_l['Ποσό'] = pd.to_numeric(st.session_state.df_l['Ποσό'], errors='coerce').fillna(0.0)
    
    lessons_today = len(st.session_state.df_l[st.session_state.df_l['Ημερομηνία'] == today])
    unpaid_total = st.session_state.df_l[(st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι")]['Ποσό'].sum()
    
    col_m1.metric("Σημερινά Μαθήματα", lessons_today)
    col_m2.metric("Εκκρεμείς Πληρωμές", f"{unpaid_total:.2f} €")
    col_m3.metric("Σύνολο Μαθητών", len(st.session_state.df_s))
    st.divider()
    
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🔄 Συγχρονισμός")
        if st.button("🔄 Sync iCloud"): 
            auto_sync()
            st.rerun()
    with c2:
        st.subheader("📅 Διαγωνίσματα")
        exams = st.session_state.df_n[st.session_state.df_n['Διαγωνίσματα'].notna() & (st.session_state.df_n['Διαγωνίσματα'] != "")]
        if not exams.empty:
            for _, r in exams.iterrows():
                st.warning(f"**{r['Μαθητής']}**: {r['Διαγωνίσματα']}")
        else: st.info("Κανένα διαγώνισμα.")

def show_finance_section():
    st.header("💰 Οικονομικά")
    tab_p, tab_r = st.tabs(["💵 Πληρωμές", "📈 Μηνιαία Αναφορά"])
    
    st.session_state.df_l['Ποσό'] = pd.to_numeric(st.session_state.df_l['Ποσό'], errors='coerce').fillna(0.0)

    with tab_p:
        with st.expander("➕ Χειροκίνητη Προσθήκη"):
            with st.form("manual_form"):
                c1, c2, c3, c4 = st.columns(4)
                sel_m = c1.selectbox("Μαθητής", st.session_state.df_s['Όνομα'].tolist()) if not st.session_state.df_s.empty else None
                d_m = c2.text_input("Ημερομηνία", date.today().strftime("%d/%m/%Y"))
                t_m = c3.text_input("Ώρα", "16:00-17:00")
                p_m = c4.number_input("Ποσό (€)", min_value=0.0, step=0.5)
                if st.form_submit_button("Προσθήκη") and sel_m:
                    uid = f"manual_{datetime.now().timestamp()}"
                    ts, te = t_m.split("-") if "-" in t_m else (t_m, t_m)
                    new_l = pd.DataFrame([[sel_m, d_m, ts, te, p_m, "Ολοκληρώθηκε", "Όχι", uid]], columns=st.session_state.df_l.columns)
                    st.session_state.df_l = pd.concat([st.session_state.df_l, new_l], ignore_index=True)
                    save_all(); st.rerun()

        unpaid = st.session_state.df_l[(st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι")].copy()
        if unpaid.empty: st.success("Όλα εξοφλημένα!")
        else:
            for i, r in unpaid.iterrows():
                c1, c2, c3 = st.columns([4, 2, 2])
                c1.write(f"**{r['Μαθητής']}** | {r['Ημερομηνία']} | {r['Ποσό']:.2f}€")
                if c2.button("✔️ Εξόφληση", key=f"pay_{i}"):
                    st.session_state.df_l.at[i, 'Πληρώθηκε'] = "Ναι"
                    save_all(); st.rerun()
                if c3.button("❌ Ακύρωση", key=f"can_{i}"):
                    st.session_state.df_l.at[i, 'Κατάσταση'] = "Ακυρώθηκε"
                    save_all(); st.rerun()

    with tab_r:
        gr_tz = ZoneInfo('Europe/Athens')
        c_m, c_y = st.columns(2)
        month = c_m.selectbox("Μήνας", list(range(1, 13)), index=datetime.now(gr_tz).month-1)
        year = c_y.selectbox("Έτος", [2025, 2026, 2027], index=1)
        
        df_m = st.session_state.df_l[st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε"].copy()
        if not df_m.empty:
            df_m['m'] = df_m['Ημερομηνία'].apply(lambda x: int(x.split('/')[1]) if '/' in str(x) else 0)
            df_m['y'] = df_m['Ημερομηνία'].apply(lambda x: int(x.split('/')[2]) if '/' in str(x) else 0)
            df_f = df_m[(df_m['m'] == month) & (df_m['y'] == year)]
            st.metric("Σύνολο Μήνα", f"{df_f['Ποσό'].sum():.2f} €")
            st.dataframe(df_f[['Μαθητής', 'Ημερομηνία', 'Ποσό', 'Πληρώθηκε']], use_container_width=True)

def show_student_management():
    st.header("👥 Μαθητές")
    if 'view_mode' not in st.session_state: st.session_state.view_mode = 'list'

    if st.session_state.view_mode == 'list':
        with st.expander("➕ Νέος Μαθητής"):
            with st.form("new_student"):
                n = st.text_input("Όνομα")
                p = st.text_input("Τηλέφωνο")
                pr = st.number_input("Τιμή/ώρα", value=15.0)
                if st.form_submit_button("Προσθήκη"):
                    new_s = pd.DataFrame([[n, p, pr]], columns=st.session_state.df_s.columns)
                    st.session_state.df_s = pd.concat([st.session_state.df_s, new_s], ignore_index=True)
                    save_all(); st.rerun()

        for i, r in st.session_state.df_s.iterrows():
            c1, c2, c3, c4 = st.columns([3, 3, 1, 1])
            if c1.button(f"👤 {r['Όνομα']}", key=f"s_{i}"):
                st.session_state.selected_student = r['Όνομα']
                st.session_state.view_mode = 'card'; st.rerun()
            c2.write(f"📞 {str(r['Τηλέφωνο'])}")
            if c3.button("✏️", key=f"ed_{i}"): pass # Προσθήκη edit αν χρειαστεί
            if c4.button("🗑️", key=f"del_{i}"):
                st.session_state.df_s = st.session_state.df_s.drop(i).reset_index(drop=True)
                save_all(); st.rerun()

    elif st.session_state.view_mode == 'card':
        sel = st.session_state.selected_student
        st.subheader(f"Καρτέλα: {sel}")
        if st.button("⬅️ Επιστροφή"): st.session_state.view_mode = 'list'; st.rerun()
        
        t1, t2 = st.tabs(["📝 Σημειώσεις & Αρχεία", "📜 Ιστορικό"])
        with t1:
            with st.form("note_form"):
                nt = st.text_area("Νέα Σημείωση")
                ex = st.date_input("Διαγώνισμα", value=None)
                if st.form_submit_button("Αποθήκευση"):
                    d = date.today().strftime("%d/%m/%Y")
                    ex_s = ex.strftime("%Y-%m-%d") if ex else ""
                    new_n = pd.DataFrame([[sel, d, nt, "", ex_s]], columns=st.session_state.df_n.columns)
                    st.session_state.df_n = pd.concat([st.session_state.df_n, new_n], ignore_index=True)
                    save_all(); st.rerun()
            
            st.write("---")
            for idx, row in st.session_state.df_n[st.session_state.df_n['Μαθητής'] == sel].iloc[::-1].iterrows():
                st.info(f"**{row['Ημερομηνία']}**: {row['Σημειώσεις']}")

def show_settings():
    st.header("⚙️ Ρυθμίσεις")
    new_url = st.text_input("iCloud Calendar URL", value=st.session_state.get('cal_url', ''))
    if st.button("Αποθήκευση URL"):
        if update_user_data(st.session_state.user, new_url):
            st.session_state.cal_url = new_url
            st.success("Το URL ενημερώθηκε!")

def main():
    st.set_page_config(page_title="MyLessons Pro", layout="wide", initial_sidebar_state="collapsed")
    
    if "auth" not in st.session_state: st.session_state.auth = False

    if not st.session_state.auth:
        st.title("📚 MyLessons Login")
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.button("Είσοδος"):
            users = get_users()
            row = users[users['username'] == u]
            if not row.empty and row['password'].values[0] == hash_pw(p):
                st.session_state.auth = True
                st.session_state.user = u
                st.session_state.cal_url = row['cal_url'].values[0]
                load_data(u)
                st.rerun()
            else: st.error("Λάθος στοιχεία")
        return

    load_data(st.session_state.user)
    
    menu = st.sidebar.radio("Μενού", ["📊 Dashboard", "📅 Πρόγραμμα", "💰 Οικονομικά", "👥 Μαθητές", "⚙️ Ρυθμίσεις"])
    if st.sidebar.button("🚪 Αποσύνδεση"): 
        st.session_state.clear()
        st.rerun()

    if menu == "📊 Dashboard": show_dashboard()
    elif menu == "📅 Πρόγραμμα":
        st.header("📅 Προσεχή Μαθήματα")
        pend = st.session_state.df_l[st.session_state.df_l['Κατάσταση'] == "Προγραμματισμένο"].copy()
        if pend.empty: st.info("Κανένα μάθημα.")
        else:
            for i, r in pend.iterrows():
                c1, c2, c3 = st.columns([4, 3, 2])
                c1.write(f"**{r['Μαθητής']}**")
                c2.write(f"{r['Ημερομηνία']} | {r['Ώρα']}")
                s_match = st.session_state.df_s[st.session_state.df_s['Όνομα'] == r['Μαθητής']]
                if not s_match.empty:
                    ph = str(s_match.iloc[0]['Τηλέφωνο']).split('.')[0]
                    msg = urllib.parse.quote(f"Υπενθύμιση: Μάθημα {r['Ημερομηνία']} στις {r['Ώρα']}.")
                    c3.link_button("📱 SMS", f"sms:{ph}?body={msg}")
                    
    elif menu == "💰 Οικονομικά": show_finance_section()
    elif menu == "👥 Μαθητές": show_student_management()
    elif menu == "⚙️ Ρυθμίσεις": show_settings()

if __name__ == "__main__":
    main()
