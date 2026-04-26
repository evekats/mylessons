import streamlit as st
import pandas as pd
import os
import requests
from icalendar import Calendar
from datetime import datetime, timedelta
import urllib.parse
import hashlib
from zoneinfo import ZoneInfo

# --- ΡΥΘΜΙΣΕΙΣ ΑΡΧΕΙΩΝ & ΦΑΚΕΛΩΝ ---
U_FILE = "users.csv"
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

# --- ΣΥΝΑΡΤΗΣΕΙΣ ΑΣΦΑΛΕΙΑΣ & ΔΙΑΧΕΙΡΙΣΗΣ ΧΡΗΣΤΩΝ ---
def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def get_users():
    if os.path.exists(U_FILE):
        return pd.read_csv(U_FILE)
    return pd.DataFrame(columns=["username", "password", "cal_url"])

def save_user(username, password, cal_url):
    df = get_users()
    if username in df['username'].values:
        return False
    new_user = pd.DataFrame([[username, hash_pw(password), cal_url]], columns=df.columns)
    pd.concat([df, new_user], ignore_index=True).to_csv(U_FILE, index=False)
    return True

def update_user_data(username, new_url, new_pw=None):
    df = get_users()
    if username in df['username'].values:
        idx = df[df['username'] == username].index[0]
        df.at[idx, 'cal_url'] = new_url
        if new_pw:
            df.at[idx, 'password'] = hash_pw(new_pw)
        df.to_csv(U_FILE, index=False)
        return True
    return False

def delete_user_account(username):
    # 1. Διαγραφή από το users.csv
    df = get_users()
    if username in df['username'].values:
        df = df[df['username'] != username]
        df.to_csv(U_FILE, index=False)
    # 2. Διαγραφή των προσωπικών του αρχείων δεδομένων
    files_to_delete = [f"{username}_students_data.csv", f"{username}_lessons_data.csv", f"{username}_student_notes.csv"]
    for f in files_to_delete:
        if os.path.exists(f): os.remove(f)

# --- ΣΥΝΑΡΤΗΣΕΙΣ ΔΕΔΟΜΕΝΩΝ (ΑΝΑ ΧΡΗΣΤΗ) ---
def load_data(username):
    s_file = f"{username}_students_data.csv"
    l_file = f"{username}_lessons_data.csv"
    n_file = f"{username}_student_notes.csv"

    if 'df_s' not in st.session_state:
        st.session_state.df_s = pd.read_csv(s_file) if os.path.exists(s_file) else pd.DataFrame(columns=["Όνομα", "Τηλέφωνο", "Τιμή"])
    if 'df_l' not in st.session_state:
        if os.path.exists(l_file):
            df_temp = pd.read_csv(l_file)
            if "UID" not in df_temp.columns: df_temp["UID"] = ""
            if "Λήξη" not in df_temp.columns: df_temp.insert(3, "Λήξη", df_temp["Ώρα"])
            st.session_state.df_l = df_temp
        else:
            st.session_state.df_l = pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Ώρα", "Λήξη", "Ποσό", "Κατάσταση", "Πληρώθηκε", "UID"])
    if 'df_n' not in st.session_state:
        st.session_state.df_n = pd.read_csv(n_file) if os.path.exists(n_file) else pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Σημειώσεις", "Αρχείο", "Διαγωνίσματα"])
    st.session_state.current_user_files = (s_file, l_file, n_file)

def save_all():
    s_f, l_f, n_f = st.session_state.current_user_files
    st.session_state.df_s.to_csv(s_f, index=False)
    st.session_state.df_l.to_csv(l_f, index=False)
    st.session_state.df_n.to_csv(n_f, index=False)

# --- ΣΥΝΑΡΤΗΣΗ ΑΥΤΟΜΑΤΟΥ ΣΥΓΧΡΟΝΙΣΜΟΥ (WIPE & REPLACE) ---
def auto_sync():
    cal_url = st.session_state.cal_url
    if not cal_url or str(cal_url) == "nan": return
    try:
        res = requests.get(cal_url, timeout=5)
        gcal = Calendar.from_ical(res.content)
        gr_tz = ZoneInfo('Europe/Athens')
        now = datetime.now(gr_tz).replace(tzinfo=None)
        
        # 1. Κλείδωμα παρελθόντος
        for i, r in st.session_state.df_l.iterrows():
            if r['Κατάσταση'] == "Προγραμματισμένο":
                try:
                    end_dt = datetime.strptime(f"{r['Ημερομηνία']} {r['Λήξη']}", "%d/%m/%Y %H:%M")
                    if now >= end_dt: st.session_state.df_l.at[i, 'Κατάσταση'] = "Ολοκληρώθηκε"
                except: pass

        # 2. Διαγραφή μελλοντικών (που προέρχονται από iCloud - όχι χειροκίνητα)
        st.session_state.df_l = st.session_state.df_l[
            (st.session_state.df_l['Κατάσταση'] != "Προγραμματισμένο") | 
            (st.session_state.df_l['UID'].str.startswith('manual_'))
        ].reset_index(drop=True)

        # 3. Σάρωση iCloud
        start_limit, end_limit = now - timedelta(days=7), now + timedelta(days=30)
        new_lessons = []
        for comp in gcal.walk('VEVENT'):
            summary, uid = str(comp.get('summary', '')), str(comp.get('uid', ''))
            if not summary.strip().lower().startswith("μάθημα"): continue
            start = comp.get('dtstart').dt
            if not isinstance(start, datetime): continue
            start = start.astimezone(gr_tz).replace(tzinfo=None)
            end = (comp.get('dtend').dt).astimezone(gr_tz).replace(tzinfo=None) if comp.get('dtend') else start + timedelta(hours=1)
            
            if start_limit <= start <= end_limit:
                match = next((s for _, s in st.session_state.df_s.iterrows() if s['Όνομα'].lower() in summary.lower()), None)
                if match is not None:
                    d_str, ts, te = start.strftime('%d/%m/%Y'), start.strftime('%H:%M'), end.strftime('%H:%M')
                    price = round(((end - start).total_seconds() / 3600) * float(match['Τιμή']), 2)
                    if now < end:
                        new_lessons.append([match['Όνομα'], d_str, ts, te, price, "Προγραμματισμένο", "Όχι", uid])
                    else:
                        if st.session_state.df_l[st.session_state.df_l['UID'] == uid].empty:
                            new_lessons.append([match['Όνομα'], d_str, ts, te, price, "Ολοκληρώθηκε", "Όχι", uid])
        if new_lessons:
            st.session_state.df_l = pd.concat([st.session_state.df_l, pd.DataFrame(new_lessons, columns=st.session_state.df_l.columns)], ignore_index=True)
        save_all()
    except: pass

# --- UI ΣΥΝΑΡΤΗΣΕΙΣ ---
def show_dashboard():
    st.header("📊 Κεντρικός Έλεγχος")
    col_m1, col_m2, col_m3 = st.columns(3)
    gr_tz = ZoneInfo('Europe/Athens')
    today = datetime.now(gr_tz).strftime('%d/%m/%Y')
    lessons_today = len(st.session_state.df_l[st.session_state.df_l['Ημερομηνία'] == today])
    unpaid_total = st.session_state.df_l[(st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι")]['Ποσό'].sum()
    col_m1.metric("Μαθήματα Σήμερα", lessons_today)
    col_m2.metric("Εκκρεμείς Πληρωμές", f"{unpaid_total} €")
    col_m3.metric("Σύνολο Μαθητών", len(st.session_state.df_s))
    st.divider()
    if st.button("🔄 Χειροκίνητος Συγχρονισμός"): auto_sync(); st.rerun()

def show_finance_section():
    st.header("💰 Οικονομικά")
    tab_p, tab_r = st.tabs(["💵 Πληρωμές", "📈 Μηνιαία Αναφορά"])
    with tab_p:
        if not st.session_state.df_s.empty:
            with st.expander("➕ Προσθήκη Χειροκίνητου Μαθήματος (Εκτός iCloud)"):
                with st.form("manual_f"):
                    c1, c2, c3, c4 = st.columns(4)
                    sm = c1.selectbox("Μαθητής", st.session_state.df_s['Όνομα'].tolist())
                    dm = c2.text_input("Ημερομηνία", datetime.now(ZoneInfo('Europe/Athens')).strftime("%d/%m/%Y"))
                    tm = c3.text_input("Ώρα (έναρξη - λήξη)", "16:00 - 17:00")
                    pm = c4.number_input("Ποσό (€)", min_value=0.0, step=5.0)
                    if st.form_submit_button("Προσθήκη"):
                        ts, te = (tm.split("-")[0].strip(), tm.split("-")[1].strip()) if "-" in tm else (tm, tm)
                        nl = pd.DataFrame([[sm, dm, ts, te, pm, "Ολοκληρώθηκε", "Όχι", f"manual_{datetime.now().timestamp()}"]], columns=st.session_state.df_l.columns)
                        st.session_state.df_l = pd.concat([st.session_state.df_l, nl], ignore_index=True); save_all(); st.success("Προστέθηκε!"); st.rerun()
        
        st.divider()
        unpaid = st.session_state.df_l[(st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι") & (st.session_state.df_l['Ποσό'] > 0)]
        for i, r in unpaid.iterrows():
            c1, c2, c3, c4 = st.columns([3, 1, 1.5, 2.5])
            c1.write(f"**{r['Μαθητής']}** ({r['Ημερομηνία']} | {r['Ώρα']} - {r['Λήξη']})")
            c2.write(f"**{r['Ποσό']}€**")
            pay_val = c3.number_input("Ποσό", min_value=0.0, value=float(r['Ποσό']), key=f"pay_{i}")
            if st.button("✔️ OK", key=f"ok_{i}"):
                st.session_state.df_l.at[i, 'Πληρώθηκε'] = "Ναι"; save_all(); st.rerun()

    with tab_r:
        gr_tz = ZoneInfo('Europe/Athens')
        c_m, c_y = st.columns(2)
        month = c_m.selectbox("Μήνας", list(range(1, 13)), index=datetime.now(gr_tz).month-1)
        year = c_y.selectbox("Έτος", [2025, 2026, 2027], index=1)
        df_all = st.session_state.df_l[st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε"].copy()
        if not df_all.empty:
            df_all['m'] = df_all['Ημερομηνία'].apply(lambda x: int(x.split('/')[1]) if '/' in str(x) else 0)
            df_all['y'] = df_all['Ημερομηνία'].apply(lambda x: int(x.split('/')[2]) if '/' in str(x) else 0)
            df_f = df_all[(df_all['m'] == month) & (df_all['y'] == year)]
            for m, row in df_f.groupby('Μαθητής'):
                with st.expander(f"{m} | Σύνολο: {row['Ποσό'].sum()}€"):
                    for _, d in row.iterrows(): st.write(f"{'✅' if d['Πληρώθηκε']=='Ναι' else '⏳'} {d['Ημερομηνία']} ({d['Ώρα']} - {d['Λήξη']}): {d['Ποσό']}€")

def show_student_management():
    st.header("👥 Μαθητές")
    with st.expander("➕ Προσθήκη Μαθητή"):
        with st.form("add_s"):
            n, ph, pr = st.text_input("Όνομα"), st.text_input("Τηλέφωνο"), st.number_input("Τιμή/ώρα", 0)
            if st.form_submit_button("Αποθήκευση"):
                st.session_state.df_s = pd.concat([st.session_state.df_s, pd.DataFrame([[n, ph, pr]], columns=st.session_state.df_s.columns)], ignore_index=True); save_all(); st.rerun()

    for i, r in st.session_state.df_s.iterrows():
        c1, c2, c3, c4, c5 = st.columns([2.5, 2, 1.5, 0.5, 0.5])
        c1.write(f"👤 {r['Όνομα']}"); c2.write(r['Τηλέφωνο']); c3.write(f"{r['Τιμή']}€/ώρα")
        if c5.button("🗑️", key=f"ds_{i}"):
            st.session_state.df_s.drop(i, inplace=True); save_all(); st.rerun()

    sel = st.selectbox("Προβολή καρτέλας:", ["-- Επιλογή --"] + st.session_state.df_s['Όνομα'].tolist())
    if sel != "-- Επιλογή --":
        t1, t2, t3 = st.tabs(["💰 Οικονομικά", "📝 Σημειώσεις", "📜 Ιστορικό"])
        with t1:
            bal = st.session_state.df_l[(st.session_state.df_l['Μαθητής'] == sel) & (st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι")]['Ποσό'].sum()
            st.metric("Υπόλοιπο", f"{bal} €")
        with t3:
            hist = st.session_state.df_l[(st.session_state.df_l['Μαθητής'] == sel) & (st.session_state.df_l['Κατάσταση'] != "Προγραμματισμένο")]
            st.dataframe(hist.iloc[::-1], use_container_width=True)

def main():
    st.set_page_config(page_title="Teacher App Pro", layout="wide", page_icon="📚")
    if "auth" not in st.session_state: st.session_state.auth = False
    if not st.session_state.auth:
        st.title("📚 Teacher App Pro")
        t1, t2 = st.tabs(["🔑 Είσοδος", "📝 Εγγραφή"])
        with t1:
            u, p = st.text_input("Username"), st.text_input("Password", type="password")
            if st.button("Είσοδος"):
                users = get_users(); row = users[users['username'] == u]
                if not row.empty and row['password'].values[0] == hash_pw(p):
                    st.session_state.auth, st.session_state.user, st.session_state.cal_url = True, u, row['cal_url'].values[0]; st.rerun()
        with t2:
            nu, np, nurl = st.text_input("Νέο User"), st.text_input("Νέο Pass", type="password"), st.text_input("iCloud Link")
            if st.button("Δημιουργία"):
                if save_user(nu, np, nurl): st.success("Έτοιμο!"); st.rerun()
        return

    if st.session_state.user not in get_users()['username'].values:
        st.session_state.clear(); st.rerun()

    load_data(st.session_state.user); auto_sync()
    st.sidebar.title(f"👤 {st.session_state.user}")
    menu = st.sidebar.radio("Μενού:", ["📊 Dashboard", "📅 Πρόγραμμα", "💰 Οικονομικά", "👥 Μαθητές", "⚙️ Ρυθμίσεις"])
    if st.sidebar.button("🚪 Αποσύνδεση"): st.session_state.clear(); st.rerun()

    if menu == "📊 Dashboard": show_dashboard()
    elif menu == "📅 Πρόγραμμα":
        st.header("📅 Πρόγραμμα")
        pend = st.session_state.df_l[st.session_state.df_l['Κατάσταση'] == "Προγραμματισμένο"]
        for i, r in pend.iterrows():
            c1, c2, c3 = st.columns([3, 4, 2])
            c1.write(f"**{r['Μαθητής']}**"); c2.write(f"{r['Ημερομηνία']} | {r['Ώρα']} - {r['Λήξη']}")
            sm = st.session_state.df_s[st.session_state.df_s['Όνομα'] == r['Μαθητής']]
            if not sm.empty: c3.link_button("📱 SMS", f"sms:{sm.iloc[0]['Τηλέφωνο']}?body={urllib.parse.quote('Υπενθύμιση μαθήματος στις ' + r['Ώρα'])}")
    elif menu == "💰 Οικονομικά": show_finance_section()
    elif menu == "👥 Μαθητές": show_student_management()
    elif menu == "⚙️ Ρυθμίσεις":
        st.header("⚙️ Ρυθμίσεις")
        with st.form("set_f"):
            nu_url = st.text_input("iCloud Link", value=st.session_state.cal_url)
            np1, np2 = st.text_input("Νέος Κωδικός", type="password"), st.text_input("Επιβεβαίωση", type="password")
            if st.form_submit_button("Αποθήκευση"):
                if np1 and np1 != np2: st.error("Λάθος κωδικός!")
                elif update_user_data(st.session_state.user, nu_url, np1 if np1 else None):
                    st.session_state.cal_url = nu_url; st.success("Αποθηκεύτηκε!"); st.rerun()
        st.divider()
        st.subheader("🚨 Διαγραφή Λογαριασμού")
        if st.checkbox("Κατανοώ ότι η διαγραφή είναι οριστική."):
            if st.button("🗑️ Οριστική Διαγραφή", type="primary"):
                delete_user_account(st.session_state.user); st.session_state.clear(); st.rerun()

if __name__ == "__main__": main()
