import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import os
import requests
from icalendar import Calendar
from datetime import datetime, timedelta
import urllib.parse
import hashlib
from zoneinfo import ZoneInfo

# --- ΣΥΝΔΕΣΗ ΜΕ GOOGLE SHEETS (mylessons) ---
def get_gsheet_client():
    try:
        creds_dict = st.secrets["gcp_service_account"]
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        return client.open("mylessons")
    except Exception as e:
        st.error(f"Σφάλμα σύνδεσης με Google Sheets: {e}")
        return None

# --- ΣΥΝΑΡΤΗΣΕΙΣ ΑΣΦΑΛΕΙΑΣ & ΔΙΑΧΕΙΡΙΣΗΣ ΧΡΗΣΤΩΝ (CLOUD) ---
def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def get_users():
    sheet = get_gsheet_client()
    if not sheet: 
        return pd.DataFrame(columns=["username", "password", "cal_url"])
    try:
        ws = sheet.worksheet("users")
        data = ws.get_all_records()
        if not data:
            return pd.DataFrame(columns=["username", "password", "cal_url"])
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"Σφάλμα κατά την ανάγνωση των χρηστών: {e}")
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
        ws = sheet.worksheet(tab)
        data = pd.DataFrame(ws.get_all_records())
        if not data.empty and 'owner' in data.columns:
            filtered = data[data['owner'] != username]
            ws.clear()
            ws.update([filtered.columns.values.tolist()] + filtered.values.tolist())

# --- ΦΟΡΤΩΣΗ & ΑΠΟΘΗΚΕΥΣΗ (ΑΝΑ ΧΡΗΣΤΗ) ---
def load_data_from_sheet(tab_name, username):
    sheet = get_gsheet_client()
    if not sheet: return pd.DataFrame()
    try:
        ws = sheet.worksheet(tab_name)
        df_all = pd.DataFrame(ws.get_all_records())
        if not df_all.empty and 'owner' in df_all.columns:
            return df_all[df_all['owner'] == username].drop(columns=['owner']).reset_index(drop=True)
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
        final_df = pd.concat([others, mine], ignore_index=True).fillna("")
        ws.clear()
        ws.update([final_df.columns.values.tolist()] + final_df.values.tolist())
    except: pass

def load_data(username):
    if 'df_s' not in st.session_state:
        st.session_state.df_s = load_data_from_sheet("students", username)
        if st.session_state.df_s.empty: st.session_state.df_s = pd.DataFrame(columns=["Όνομα", "Τηλέφωνο", "Τιμή"])
    if 'df_l' not in st.session_state:
        st.session_state.df_l = load_data_from_sheet("lessons", username)
        if st.session_state.df_l.empty: st.session_state.df_l = pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Ώρα", "Λήξη", "Ποσό", "Κατάσταση", "Πληρώθηκε", "UID"])
    if 'df_n' not in st.session_state:
        st.session_state.df_n = load_data_from_sheet("notes", username)
        if st.session_state.df_n.empty: st.session_state.df_n = pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Σημειώσεις", "Αρχείο", "Διαγωνίσματα"])

def save_all():
    user = st.session_state.user
    save_data_to_sheet(st.session_state.df_s, "students", user)
    save_data_to_sheet(st.session_state.df_l, "lessons", user)
    save_data_to_sheet(st.session_state.df_n, "notes", user)

def auto_sync():
    cal_url = st.session_state.cal_url
    if not cal_url or str(cal_url) == "nan": return
    try:
        res = requests.get(cal_url, timeout=5)
        gcal = Calendar.from_ical(res.content)
        gr_tz = ZoneInfo('Europe/Athens')
        now = datetime.now(gr_tz).replace(tzinfo=None)

        for i, r in st.session_state.df_l.iterrows():
            if r['Κατάσταση'] == "Προγραμματισμένο":
                try:
                    end_dt = datetime.strptime(f"{r['Ημερομηνία']} {r['Λήξη']}", "%d/%m/%Y %H:%M")
                    if now >= end_dt: st.session_state.df_l.at[i, 'Κατάσταση'] = "Ολοκληρώθηκε"
                except: pass

        st.session_state.df_l = st.session_state.df_l[
            (st.session_state.df_l['Κατάσταση'] != "Προγραμματισμένο") | 
            (st.session_state.df_l['UID'].astype(str).str.startswith('manual_'))
        ].reset_index(drop=True)

        start_limit, end_limit = now - timedelta(days=7), now + timedelta(days=30)
        new_lessons = []
        for comp in gcal.walk('VEVENT'):
            summary, uid = str(comp.get('summary', '')), str(comp.get('uid', ''))
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
                    price = round(((end - start).total_seconds() / 3600) * float(match['Τιμή']), 2)
                    if now < end:
                        new_lessons.append([match['Όνομα'], d_str, t_start, t_end, price, "Προγραμματισμένο", "Όχι", uid])
                    else:
                        if st.session_state.df_l[st.session_state.df_l['UID'] == uid].empty:
                            new_lessons.append([match['Όνομα'], d_str, t_start, t_end, price, "Ολοκληρώθηκε", "Όχι", uid])
        if new_lessons:
            new_df = pd.DataFrame(new_lessons, columns=st.session_state.df_l.columns)
            st.session_state.df_l = pd.concat([st.session_state.df_l, new_df], ignore_index=True)
        save_all()
    except: pass

def show_dashboard():
    st.header("📊 Dashboard")
    col_m1, col_m2, col_m3 = st.columns(3)
    gr_tz = ZoneInfo('Europe/Athens')
    today = datetime.now(gr_tz).strftime('%d/%m/%Y')
    lessons_today = len(st.session_state.df_l[st.session_state.df_l['Ημερομηνία'] == today])
    unpaid_total = st.session_state.df_l[(st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι")]['Ποσό'].sum()
    col_m1.metric("Σημερινά Μαθήματα", lessons_today)
    col_m2.metric("Εκκρεμείς Πληρωμές", f"{unpaid_total} €")
    col_m3.metric("Σύνολο Μαθητών", len(st.session_state.df_s))
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("☁️ iCloud Sync")
        if st.button("🔄 Sync Now"): auto_sync(); st.rerun()
    with c2:
        st.subheader("📅 Διαγωνίσματα")
        exams = st.session_state.df_n[st.session_state.df_n['Διαγωνίσματα'].notna() & (st.session_state.df_n['Διαγωνίσματα'] != "")]
        if not exams.empty:
            for _, r in exams.iterrows(): st.warning(f"**{r['Μαθητής']}**: {r['Διαγωνίσματα']}")
        else: st.info("Κανένα διαγώνισμα.")

def show_finance_section():
    st.header("💰 Οικονομικά")
    tab_p, tab_r = st.tabs(["💵 Πληρωμές", "📈 Μηνιαία Αναφορά"])
    with tab_p:
        if not st.session_state.df_s.empty:
            with st.expander("➕ Προσθήκη Μαθήματος (Εκτός iCloud)"):
                with st.form("manual_lesson_form"):
                    c1, c2, c3, c4 = st.columns(4)
                    sel_m = c1.selectbox("Μαθητής", st.session_state.df_s['Όνομα'].tolist())
                    d_m = c2.text_input("Ημερομηνία", datetime.now(ZoneInfo('Europe/Athens')).strftime("%d/%m/%Y"))
                    t_m = c3.text_input("Ώρα (έναρξη - λήξη)", "16:00 - 17:00")
                    p_m = c4.number_input("Ποσό (€)", min_value=0.0, step=5.0)
                    if st.form_submit_button("Προσθήκη"):
                        uid_m = f"manual_{datetime.now().timestamp()}"
                        ts, te = (t_m.split("-")[0].strip(), t_m.split("-")[1].strip()) if "-" in t_m else (t_m, t_m)
                        new_l = pd.DataFrame([[sel_m, d_m, ts, te, p_m, "Ολοκληρώθηκε", "Όχι", uid_m]], columns=st.session_state.df_l.columns)
                        st.session_state.df_l = pd.concat([st.session_state.df_l, new_l], ignore_index=True)
                        save_all(); st.success("Το μάθημα προστέθηκε!"); st.rerun()
        st.divider()
        st.session_state.df_l = st.session_state.df_l.reset_index(drop=True)
        unpaid = st.session_state.df_l[(st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι") & (st.session_state.df_l['Ποσό'] > 0)].copy()
        if unpaid.empty: st.success("Όλα εξοφλημένα!")
        else:
            unpaid['temp_dt'] = pd.to_datetime(unpaid['Ημερομηνία'] + " " + unpaid['Ώρα'], format="%d/%m/%Y %H:%M", errors='coerce')
            unpaid = unpaid.sort_values('temp_dt').drop(columns=['temp_dt'])
            for i, r in unpaid.iterrows():
                c1, c2, c3, c4 = st.columns([3, 1.5, 1.5, 2.5])
                c1.write(f"**{r['Μαθητής']}** ({r['Ημερομηνία']})")
                if st.session_state.get(f"edit_{i}"):
                    new_amt = c2.number_input("Νέο Ποσό", value=float(r['Ποσό']), key=f"new_{i}")
                    if c2.button("💾", key=f"sv_{i}"):
                        st.session_state.df_l.at[i, 'Ποσό'] = float(new_amt)
                        st.session_state[f"edit_{i}"] = False; save_all(); st.rerun()
                else:
                    c2.write(f"**{r['Ποσό']}€**")
                    if c2.button("✏️", key=f"ed_{i}"): st.session_state[f"edit_{i}"] = True; st.rerun()
                pay_val = c3.number_input("Πληρωμή", min_value=0.0, value=float(r['Ποσό']), key=f"pay_{i}")
                cp1, cp2 = c4.columns(2)
                if cp1.button("✔️", key=f"ok_{i}"):
                    surplus = float(pay_val) - float(r['Ποσό'])
                    st.session_state.df_l.at[i, 'Πληρώθηκε'] = "Ναι"
                    if surplus != 0:
                        new_adj = pd.DataFrame([[r['Μαθητής'], r['Ημερομηνία'], r['Ώρα'], r['Λήξη'], -surplus, "Ολοκληρώθηκε", "Όχι", f"adj_{i}"]], columns=st.session_state.df_l.columns)
                        st.session_state.df_l = pd.concat([st.session_state.df_l, new_adj], ignore_index=True)
                    save_all(); st.rerun()
                if cp2.button("❌", key=f"can_{i}"):
                    st.session_state.df_l.at[i, 'Κατάσταση'] = "Ακυρώθηκε"; save_all(); st.rerun()

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
            if not df_f.empty:
                st.metric("💶 Συνολικά Έσοδα Μήνα", f"{df_f['Ποσό'].sum():.2f} €")
                summary = df_f.groupby('Μαθητής').agg({'Ποσό': 'sum', 'Ημερομηνία': 'count'}).reset_index()
                for _, row in summary.iterrows():
                    with st.expander(f"{row['Μαθητής']} | Σύνολο: {row['Ποσό']}€"):
                        for _, det in df_f[df_f['Μαθητής'] == row['Μαθητής']].iterrows():
                            st.write(f"{'✅' if det['Πληρώθηκε']=='Ναι' else '⏳'} {det['Ημερομηνία']}: {det['Ποσό']}€")

def show_student_management():
    st.header("👥 Μαθητές")
    
    # CSS για Clickable Links (κουμπιά που μοιάζουν με link)
    st.markdown("""
        <style>
        div.stButton > button:first-child {
            border: none; background-color: transparent; color: #007bff;
            text-decoration: underline; padding: 0; height: auto; font-weight: bold;
        }
        div.stButton > button:hover { color: #0056b3; background-color: transparent; }
        </style>
    """, unsafe_allow_html=True)

    if 'selected_student' not in st.session_state:
        st.session_state.selected_student = None

    with st.expander("➕ Προσθήκη Μαθητή"):
        with st.form("add_s"):
            n, ph, pr = st.text_input("Όνομα"), st.text_input("Τηλέφωνο"), st.number_input("Τιμή/ώρα", 0)
            if st.form_submit_button("Αποθήκευση"):
                st.session_state.df_s = pd.concat([st.session_state.df_s, pd.DataFrame([[n, ph, pr]], columns=st.session_state.df_s.columns)], ignore_index=True)
                save_all(); st.rerun()

    for i, r in st.session_state.df_s.iterrows():
        c1, c2, c3, c4, c5 = st.columns([2.5, 2, 1.5, 0.5, 0.5])
        # Ενέργεια: Click στο όνομα για άνοιγμα καρτέλας
        if c1.button(f"👤 {r['Όνομα']}", key=f"n_btn_{i}"):
            st.session_state.selected_student = r['Όνομα']
            st.rerun()
        c2.write(r['Τηλέφωνο'])
        c3.write(f"{r['Τιμή']}€/ώρα")
        if c4.button("✏️", key=f"e_{i}"): st.session_state.edit_idx = i; st.rerun()
        if c5.button("🗑️", key=f"d_{i}"):
            st.session_state.df_l = st.session_state.df_l[st.session_state.df_l['Μαθητής'] != r['Όνομα']]
            st.session_state.df_s = st.session_state.df_s.drop(i).reset_index(drop=True)
            save_all(); st.rerun()
    
    st.divider()
    
    # Εμφάνιση καρτέλας αν έχει επιλεγεί μαθητής
    if st.session_state.selected_student:
        sel = st.session_state.selected_student
        col_t, col_cl = st.columns([0.8, 0.2])
        col_t.subheader(f"📂 Καρτέλα: {sel}")
        if col_cl.button("❌ Κλείσιμο"):
            st.session_state.selected_student = None
            st.rerun()

        t1, t2, t3 = st.tabs(["💰 Οικονομικά & SMS", "📝 Σημειώσεις & Αρχεία", "📜 Ιστορικό"])
        with t2:
            with st.form("n_f", clear_on_submit=True):
                note, exam = st.text_area("Σημειώσεις"), st.text_input("Διαγώνισμα")
                if st.form_submit_button("Αποθήκευση"):
                    d_n = datetime.now(ZoneInfo('Europe/Athens')).strftime('%d/%m/%Y')
                    new_n = pd.DataFrame([[sel, d_n, note, "", exam]], columns=st.session_state.df_n.columns)
                    st.session_state.df_n = pd.concat([st.session_state.df_n, new_n], ignore_index=True); save_all(); st.rerun()
            for _, nr in st.session_state.df_n[st.session_state.df_n['Μαθητής'] == sel].iloc[::-1].iterrows():
                with st.expander(f"📅 {nr['Ημερομηνία']}"):
                    st.write(nr['Σημειώσεις'])
                    if nr['Διαγωνίσματα']: st.error(f"🚩 {nr['Διαγωνίσματα']}")

# --- ΣΥΝΑΡΤΗΣΗ ΡΥΘΜΙΣΕΩΝ ---
def show_settings():
    st.header("⚙️ Ρυθμίσεις Λογαριασμού")
    with st.expander("🔗 Ενημέρωση iCloud Link & Password"):
        with st.form("update_u"):
            new_url = st.text_input("Νέο iCloud Link", value=st.session_state.cal_url)
            new_pw = st.text_input("Νέος Κωδικός (άστο κενό αν δεν αλλάζεις)", type="password")
            if st.form_submit_button("Αποθήκευση"):
                if update_user_data(st.session_state.user, new_url, new_pw if new_pw else None):
                    st.session_state.cal_url = new_url
                    st.success("Τα στοιχεία ενημερώθηκαν!")
                else: 
                    st.error("Σφάλμα ενημέρωσης.")
    
    if st.button("🔴 Διαγραφή Λογαριασμού", type="primary"):
        delete_user_account(st.session_state.user)
        st.session_state.clear()
        st.rerun()

# --- ΚΥΡΙΑ ΕΦΑΡΜΟΓΗ ---
def main():
    # Ενέργεια: Αυτόματο κλείσιμο μενού
    st.set_page_config(page_title="MyLessons Pro", layout="wide", page_icon="📚", initial_sidebar_state="collapsed")
    
    if "auth" not in st.session_state: 
        st.session_state.auth = False
    
    if not st.session_state.auth:
        st.title("📚 MyLessons")
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            tab_login, tab_signup = st.tabs(["🔑 Log in", "📝 Sign up"])
            with tab_login:
                u, p = st.text_input("Username"), st.text_input("Password", type="password")
                if st.button("Log in", use_container_width=True):
                    users = get_users()
                    row = users[users['username'] == u]
                    if not row.empty and row['password'].values[0] == hash_pw(p):
                        st.session_state.auth, st.session_state.user, st.session_state.cal_url = True, u, row['cal_url'].values[0]
                        load_data(u)
                        st.rerun()
                    else: 
                        st.error("Λάθος στοιχεία!")
            with tab_signup:
                nu, np, nurl = st.text_input("Νέο User"), st.text_input("Νέο Pass", type="password"), st.text_input("iCloud Link")
                if st.button("Δημιουργία", use_container_width=True):
                    if save_user(nu, np, nurl): 
                        st.success("Έτοιμο!")
                        st.rerun()
        return

    # Έλεγχος αν ο χρήστης υπάρχει ακόμα στη βάση
    users_df = get_users()
    if st.session_state.user not in users_df['username'].values:
        st.session_state.clear()
        st.rerun()

    load_data(st.session_state.user)
    auto_sync()
    
    st.sidebar.title(f"👤 {st.session_state.user}")
    menu = st.sidebar.radio("Μενού:", ["📊 Dashboard", "📅 Πρόγραμμα", "💰 Οικονομικά", "👥 Μαθητές", "⚙️ Ρυθμίσεις"])
    if st.sidebar.button("🚪 Log out"): 
        st.session_state.clear()
        st.rerun()

    if menu == "📊 Dashboard": 
        show_dashboard()
    elif menu == "📅 Πρόγραμμα":
        st.header("📅 Πρόγραμμα")
        pend = st.session_state.df_l[st.session_state.df_l['Κατάσταση'] == "Προγραμματισμένο"].copy()
        if pend.empty: 
            st.success("Δεν υπάρχουν προγραμματισμένα μαθήματα.")
        else:
            for i, r in pend.iterrows():
                c1, c2, c3 = st.columns([3, 4, 2])
                c1.write(f"**{r['Μαθητής']}**")
                c2.write(f"{r['Ημερομηνία']} | {r['Ώρα']}")
                s_match = st.session_state.df_s[st.session_state.df_s['Όνομα'] == r['Μαθητής']]
                if not s_match.empty:
                    msg = urllib.parse.quote(f"Υπενθυμίζω το μάθημα μας στις {r['Ώρα']}.")
                    c3.link_button("📱 SMS", f"sms:{s_match.iloc[0]['Τηλέφωνο']}?body={msg}")
    elif menu == "💰 Οικονομικά": 
        show_finance_section()
    elif menu == "👥 Μαθητές": 
        show_student_management()
    elif menu == "⚙️ Ρυθμίσεις":
        show_settings()

if __name__ == "__main__":
    main()
