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

# --- ΡΥΘΜΙΣΕΙΣ GOOGLE SHEETS & ΦΑΚΕΛΩΝ ---
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

def get_gsheet_client():
    try:
        creds_dict = st.secrets["gcp_service_account"]
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        return client.open("TeacherDatabase")
    except Exception as e:
        st.error(f"Σφάλμα σύνδεσης με Google Sheets: {e}")
        return None

# --- ΣΥΝΑΡΤΗΣΕΙΣ ΑΣΦΑΛΕΙΑΣ & ΔΙΑΧΕΙΡΙΣΗΣ ΧΡΗΣΤΩΝ (CLOUD) ---
def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def get_users_df():
    sheet = get_gsheet_client()
    if sheet:
        ws = sheet.worksheet("users")
        return pd.DataFrame(ws.get_all_records())
    return pd.DataFrame(columns=["username", "password", "cal_url"])

def save_user(username, password, cal_url):
    sheet = get_gsheet_client()
    if not sheet: return False
    ws = sheet.worksheet("users")
    df = pd.DataFrame(ws.get_all_records())
    if username in df['username'].values:
        return False
    ws.append_row([username, hash_pw(password), cal_url])
    return True

def update_user_data(username, new_url, new_pw=None):
    sheet = get_gsheet_client()
    if not sheet: return False
    ws = sheet.worksheet("users")
    df = pd.DataFrame(ws.get_all_records())
    if username in df['username'].values:
        idx = df[df['username'] == username].index[0]
        # Το gspread ξεκινάει από 1, και έχουμε header, άρα idx + 2
        ws.update_cell(idx + 2, 3, new_url)
        if new_pw:
            ws.update_cell(idx + 2, 2, hash_pw(new_pw))
        return True
    return False

def delete_user_account(username):
    sheet = get_gsheet_client()
    if not sheet: return
    # 1. Διαγραφή από το users sheet
    ws_u = sheet.worksheet("users")
    users_all = pd.DataFrame(ws_u.get_all_records())
    if username in users_all['username'].values:
        idx = users_all[users_all['username'] == username].index[0]
        ws_u.delete_rows(int(idx + 2))
    
    # 2. Διαγραφή των δεδομένων του από τα άλλα tabs
    for tab in ["students", "lessons", "notes"]:
        ws = sheet.worksheet(tab)
        data = pd.DataFrame(ws.get_all_records())
        if not data.empty and 'owner' in data.columns:
            filtered = data[data['owner'] != username]
            ws.clear()
            ws.update([filtered.columns.values.tolist()] + filtered.values.tolist())

# --- ΣΥΝΑΡΤΗΣΕΙΣ ΔΕΔΟΜΕΝΩΝ (CLOUD ΑΠΟΘΗΚΕΥΣΗ) ---
def load_data(username):
    sheet = get_gsheet_client()
    if not sheet: return

    # Φόρτωση Μαθητών
    ws_s = sheet.worksheet("students")
    df_all_s = pd.DataFrame(ws_s.get_all_records())
    st.session_state.df_s = df_all_s[df_all_s['owner'] == username].drop(columns=['owner']).reset_index(drop=True) if not df_all_s.empty else pd.DataFrame(columns=["Όνομα", "Τηλέφωνο", "Τιμή"])

    # Φόρτωση Μαθημάτων
    ws_l = sheet.worksheet("lessons")
    df_all_l = pd.DataFrame(ws_l.get_all_records())
    if not df_all_l.empty:
        df_temp = df_all_l[df_all_l['owner'] == username].drop(columns=['owner']).reset_index(drop=True)
        if "UID" not in df_temp.columns: df_temp["UID"] = ""
        if "Λήξη" not in df_temp.columns: df_temp.insert(3, "Λήξη", df_temp["Ώρα"])
        st.session_state.df_l = df_temp
    else:
        st.session_state.df_l = pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Ώρα", "Λήξη", "Ποσό", "Κατάσταση", "Πληρώθηκε", "UID"])

    # Φόρτωση Σημειώσεων
    ws_n = sheet.worksheet("notes")
    df_all_n = pd.DataFrame(ws_n.get_all_records())
    st.session_state.df_n = df_all_n[df_all_n['owner'] == username].drop(columns=['owner']).reset_index(drop=True) if not df_all_n.empty else pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Σημειώσεις", "Αρχείο", "Διαγωνίσματα"])

def save_to_sheet(df, worksheet_name):
    sheet = get_gsheet_client()
    if not sheet: return
    ws = sheet.worksheet(worksheet_name)
    username = st.session_state.user
    
    all_data = pd.DataFrame(ws.get_all_records())
    if not all_data.empty and 'owner' in all_data.columns:
        all_data = all_data[all_data['owner'] != username]
    
    temp_df = df.copy()
    temp_df.insert(0, 'owner', username)
    updated_df = pd.concat([all_data, temp_df], ignore_index=True).fillna("")
    
    ws.clear()
    ws.update([updated_df.columns.values.tolist()] + updated_df.values.tolist())

def save_all():
    save_to_sheet(st.session_state.df_s, "students")
    save_to_sheet(st.session_state.df_l, "lessons")
    save_to_sheet(st.session_state.df_n, "notes")

# --- ΣΥΝΑΡΤΗΣΗ ΑΥΤΟΜΑΤΟΥ ΣΥΓΧΡΟΝΙΣΜΟΥ (Όπως την είχες) ---
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
            (st.session_state.df_l['Κατάσταση'] != "Προγραμματισμένο") | (st.session_state.df_l['UID'].str.startswith('manual_'))
        ].reset_index(drop=True)

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

            if (now - timedelta(days=7)) <= start <= (now + timedelta(days=30)):
                match = next((s for _, s in st.session_state.df_s.iterrows() if s['Όνομα'].lower() in summary.lower()), None)
                if match is not None:
                    d_str, t_s, t_e = start.strftime('%d/%m/%Y'), start.strftime('%H:%M'), end.strftime('%H:%M')
                    p = round(((end - start).total_seconds() / 3600) * float(match['Τιμή']), 2)
                    if now < end:
                        new_lessons.append([match['Όνομα'], d_str, t_s, t_e, p, "Προγραμματισμένο", "Όχι", uid])
                    else:
                        exists = not st.session_state.df_l[st.session_state.df_l['UID'] == uid].empty
                        if not exists:
                            new_lessons.append([match['Όνομα'], d_str, t_s, t_e, p, "Ολοκληρώθηκε", "Όχι", uid])

        if new_lessons:
            new_df = pd.DataFrame(new_lessons, columns=st.session_state.df_l.columns)
            st.session_state.df_l = pd.concat([st.session_state.df_l, new_df], ignore_index=True)
        save_all()
    except: pass

# --- UI ΣΥΝΑΡΤΗΣΕΙΣ ---
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
        if st.button("🔄 Sync Now"):
            auto_sync(); st.rerun()
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
            for i, r in unpaid.iterrows():
                c1, c2, c3, c4 = st.columns([3, 1.5, 1.5, 2.5])
                c1.write(f"**{r['Μαθητής']}** ({r['Ημερομηνία']})")
                c2.write(f"**{r['Ποσό']}€**")
                pay_val = c3.number_input("Πληρωμή", min_value=0.0, value=float(r['Ποσό']), key=f"pay_{i}")
                cp1, cp2 = c4.columns(2)
                if cp1.button("✔️", key=f"ok_{i}"):
                    st.session_state.df_l.at[i, 'Πληρώθηκε'] = "Ναι"; save_all(); st.rerun()
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
                st.dataframe(df_f[['Μαθητής', 'Ημερομηνία', 'Ποσό', 'Πληρώθηκε']], use_container_width=True)

def show_student_management():
    st.header("👥 Μαθητές")
    with st.expander("➕ Προσθήκη Μαθητή"):
        with st.form("add_s"):
            n, ph, pr = st.text_input("Όνομα"), st.text_input("Τηλέφωνο"), st.number_input("Τιμή/ώρα", 0)
            if st.form_submit_button("Αποθήκευση"):
                st.session_state.df_s = pd.concat([st.session_state.df_s, pd.DataFrame([[n, ph, pr]], columns=st.session_state.df_s.columns)], ignore_index=True)
                save_all(); st.rerun()

    for i, r in st.session_state.df_s.iterrows():
        c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
        c1.write(f"👤 {r['Όνομα']}"); c2.write(r['Τηλέφωνο']); c3.write(f"{r['Τιμή']}€/ώρα")
        if c4.button("🗑️", key=f"d_{i}"):
            st.session_state.df_s = st.session_state.df_s.drop(i).reset_index(drop=True)
            save_all(); st.rerun()

    st.divider()
    sel = st.selectbox("Καρτέλα Μαθητή:", ["-- Επιλογή --"] + st.session_state.df_s['Όνομα'].tolist())
    if sel != "-- Επιλογή --":
        t1, t2 = st.tabs(["💰 Οικονομικά & SMS", "📝 Σημειώσεις"])
        with t1:
            balance = st.session_state.df_l[(st.session_state.df_l['Μαθητής'] == sel) & (st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι")]['Ποσό'].sum()
            st.metric("Υπόλοιπο", f"{balance} €")
            s_match = st.session_state.df_s[st.session_state.df_s['Όνομα'] == sel]
            if not s_match.empty:
                msg = urllib.parse.quote(f"Γεια σας, το υπόλοιπο για τα μαθήματα είναι {balance}€. Ευχαριστώ!")
                st.link_button("📱 Αποστολή SMS", f"sms:{s_match.iloc[0]['Τηλέφωνο']}?body={msg}")
        with t2:
            with st.form("notes_form"):
                txt = st.text_area("Νέα Σημείωση")
                if st.form_submit_button("Αποθήκευση"):
                    d = datetime.now().strftime('%d/%m/%Y')
                    new_n = pd.DataFrame([[sel, d, txt, "", ""]], columns=st.session_state.df_n.columns)
                    st.session_state.df_n = pd.concat([st.session_state.df_n, new_n], ignore_index=True)
                    save_all(); st.rerun()
            for _, nr in st.session_state.df_n[st.session_state.df_n['Μαθητής'] == sel].iloc[::-1].iterrows():
                st.info(f"📅 {nr['Ημερομηνία']}: {nr['Σημειώσεις']}")

# --- ΚΥΡΙΑ ΕΦΑΡΜΟΓΗ ---
def main():
    st.set_page_config(page_title="Teacher App Pro", layout="wide", page_icon="📚")
    if "auth" not in st.session_state: st.session_state.auth = False
    
    if not st.session_state.auth:
        st.title("📚 MyLessons Cloud")
        tab_login, tab_signup = st.tabs(["🔑 Log in", "📝 Sign up"])
        with tab_login:
            u, p = st.text_input("Username"), st.text_input("Password", type="password")
            if st.button("Log in"):
                users = get_users_df()
                if not users.empty and u in users['username'].values:
                    row = users[users['username'] == u].iloc[0]
                    if row['password'] == hash_pw(p):
                        st.session_state.auth, st.session_state.user, st.session_state.cal_url = True, u, row['cal_url']
                        load_data(u); st.rerun()
                st.error("Λάθος στοιχεία!")
        with tab_signup:
            nu, np, nurl = st.text_input("Νέο User"), st.text_input("Νέο Pass", type="password"), st.text_input("iCloud Link")
            if st.button("Δημιουργία"):
                if save_user(nu, np, nurl): st.success("Έτοιμο!"); st.rerun()
        return

    load_data(st.session_state.user); auto_sync()
    st.sidebar.title(f"👤 {st.session_state.user}")
    menu = st.sidebar.radio("Μενού:", ["📊 Dashboard", "📅 Πρόγραμμα", "💰 Οικονομικά", "👥 Μαθητές", "⚙️ Ρυθμίσεις"])
    if st.sidebar.button("🚪 Log out"): st.session_state.clear(); st.rerun()

    if menu == "📊 Dashboard": show_dashboard()
    elif menu == "📅 Πρόγραμμα":
        st.header("📅 Πρόγραμμα")
        pend = st.session_state.df_l[st.session_state.df_l['Κατάσταση'] == "Προγραμματισμένο"].copy()
        if pend.empty: st.success("Δεν υπάρχουν μαθήματα.")
        else:
            for i, r in pend.iterrows():
                c1, c2, c3 = st.columns([3, 4, 2])
                c1.write(f"**{r['Μαθητής']}**")
                c2.write(f"{r['Ημερομηνία']} | {r['Ώρα']}")
                s_match = st.session_state.df_s[st.session_state.df_s['Όνομα'] == r['Μαθητής']]
                if not s_match.empty:
                    msg = urllib.parse.quote(f"Υπενθύμιση μαθήματος: {r['Ημερομηνία']} στις {r['Ώρα']}.")
                    c3.link_button("📱 SMS", f"sms:{s_match.iloc[0]['Τηλέφωνο']}?body={msg}")
    elif menu == "💰 Οικονομικά": show_finance_section()
    elif menu == "👥 Μαθητές": show_student_management()
    elif menu == "⚙️ Ρυθμίσεις":
        st.header("⚙️ Ρυθμίσεις")
        new_url = st.text_input("iCloud Link", value=st.session_state.cal_url)
        if st.button("Αποθήκευση"):
            if update_user_data(st.session_state.user, new_url):
                st.session_state.cal_url = new_url; st.success("Αποθηκεύτηκε!")
        if st.button("🗑️ Διαγραφή Λογαριασμού", type="primary"):
            delete_user_account(st.session_state.user); st.session_state.clear(); st.rerun()

if __name__ == "__main__":
    main()
