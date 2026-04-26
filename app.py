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

    # 2. Διαγραφή των προσωπικών του αρχείων (CSV)
    files_to_delete = [
        f"{username}_students_data.csv",
        f"{username}_lessons_data.csv",
        f"{username}_student_notes.csv"
    ]
    for f in files_to_delete:
        if os.path.exists(f):
            os.remove(f)

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

        # 1. ΚΛΕΙΔΩΜΑ ΠΑΡΕΛΘΟΝΤΟΣ: Αν ένα "Προγραμματισμένο" πέρασε, το κάνουμε "Ολοκληρώθηκε" για να μην σβηστεί
        for i, r in st.session_state.df_l.iterrows():
            if r['Κατάσταση'] == "Προγραμματισμένο":
                try:
                    end_dt = datetime.strptime(f"{r['Ημερομηνία']} {r['Λήξη']}", "%d/%m/%Y %H:%M")
                    if now >= end_dt:
                        st.session_state.df_l.at[i, 'Κατάσταση'] = "Ολοκληρώθηκε"
                except: pass

        # 2. ΣΚΟΥΠΙΣΜΑ: Διαγράφουμε όλα τα μελλοντικά μαθήματα από την εφαρμογή
        st.session_state.df_l = st.session_state.df_l[st.session_state.df_l['Κατάσταση'] != "Προγραμματισμένο"].reset_index(drop=True)

        # 3. ΣΑΡΩΣΗ & ΠΡΟΣΘΗΚΗ: Φέρνουμε όλα τα φρέσκα δεδομένα από το iCloud
        start_limit = now - timedelta(days=7)
        end_limit = now + timedelta(days=30)
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
                    price = round(((end - start).total_seconds() / 3600) * float(match['Τιμή']), 2)

                    if now < end:
                        # Είναι στο μέλλον: Το προσθέτουμε ως Προγραμματισμένο
                        new_lessons.append([match['Όνομα'], d_str, t_start, t_end, price, "Προγραμματισμένο", "Όχι", uid])
                    else:
                        # Είναι στο παρελθόν: Αν για κάποιο λόγο δεν υπάρχει ήδη στα ολοκληρωμένα, το βάζουμε
                        exists = not st.session_state.df_l[st.session_state.df_l['UID'] == uid].empty
                        if not exists:
                            new_lessons.append([match['Όνομα'], d_str, t_start, t_end, price, "Ολοκληρώθηκε", "Όχι", uid])

        # Ενώνουμε τα παλιά (που κρατήσαμε) με τα καινούργια
        if new_lessons:
            new_df = pd.DataFrame(new_lessons, columns=st.session_state.df_l.columns)
            st.session_state.df_l = pd.concat([st.session_state.df_l, new_df], ignore_index=True)

        save_all()
    except Exception as e:
        pass

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
        st.success("Το πρόγραμμα ενημερώνεται αυτόματα.")
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
        # ΧΕΙΡΟΚΙΝΗΤΗ ΠΡΟΣΘΗΚΗ ΜΑΘΗΜΑΤΟΣ
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
                        if "-" in t_m:
                            t_start = t_m.split("-")[0].strip()
                            t_end = t_m.split("-")[1].strip()
                        else:
                            t_start = t_m
                            t_end = t_m
                        new_l = pd.DataFrame([[sel_m, d_m, t_start, t_end, p_m, "Ολοκληρώθηκε", "Όχι", uid_m]], columns=st.session_state.df_l.columns)
                        st.session_state.df_l = pd.concat([st.session_state.df_l, new_l], ignore_index=True)
                        save_all()
                        st.success("Το μάθημα προστέθηκε!")
                        st.rerun()

        st.divider()
        unpaid = st.session_state.df_l[(st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι") & (st.session_state.df_l['Ποσό'] > 0)].copy()
        if not unpaid.empty:
            unpaid['temp_dt'] = pd.to_datetime(unpaid['Ημερομηνία'] + " " + unpaid['Ώρα'], format="%d/%m/%Y %H:%M", errors='coerce')
            unpaid = unpaid.sort_values('temp_dt').drop(columns=['temp_dt'])
        if unpaid.empty: st.success("Όλα εξοφλημένα!")
        for i, r in unpaid.iterrows():
            c1, c2, c3, c4 = st.columns([3, 1, 1.5, 2.5])
            c1.write(f"**{r['Μαθητής']}** ({r['Ημερομηνία']} | {r['Ώρα']} - {r['Λήξη']})")
            c2.write(f"**{r['Ποσό']}€**")
            pay_val = c3.number_input("Ποσό", min_value=0.0, value=float(r['Ποσό']), key=f"pay_{i}", step=5.0)
            cp1, cp2 = c4.columns(2)
            if cp1.button("✔️", key=f"ok_{i}"):
                surplus = pay_val - float(r['Ποσό'])
                st.session_state.df_l.at[i, 'Πληρώθηκε'] = "Ναι"
                if surplus > 0:
                    others = st.session_state.df_l[(st.session_state.df_l['Μαθητής'] == r['Μαθητής']) & (st.session_state.df_l['Πληρώθηκε'] == "Όχι") & (st.session_state.df_l.index != i)]
                    for idx in others.index:
                        if surplus <= 0: break
                        val = st.session_state.df_l.at[idx, 'Ποσό']
                        if surplus >= val:
                            st.session_state.df_l.at[idx, 'Πληρώθηκε'] = "Ναι"; surplus -= val
                        else:
                            st.session_state.df_l.at[idx, 'Ποσό'] -= surplus; surplus = 0
                    if surplus > 0:
                        new_cr = pd.DataFrame([[r['Μαθητής'], r['Ημερομηνία'], "00:00", "00:00", -surplus, "Ολοκληρώθηκε", "Όχι", "credit"]], columns=st.session_state.df_l.columns)
                        st.session_state.df_l = pd.concat([st.session_state.df_l, new_cr], ignore_index=True)
                save_all(); st.rerun()
            if cp2.button("❌ ", key=f"can_{i}"):
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
            summary = df_f.groupby('Μαθητής').agg({'Ποσό': 'sum', 'Ημερομηνία': 'count'}).reset_index()
            for _, row in summary.iterrows():
                with st.expander(f"{row['Μαθητής']} | {row['Ημερομηνία']} Μαθήματα | Σύνολο: {row['Ποσό']}€"):
                    for _, det in df_f[df_f['Μαθητής'] == row['Μαθητής']].iterrows():
                        st.write(f"{'✅' if det['Πληρώθηκε']=='Ναι' else '⏳'} {det['Ημερομηνία']} ({det['Ώρα']} - {det['Λήξη']}): {det['Ποσό']}€")

def show_student_management():
    st.header("👥 Μαθητές")
    with st.expander("➕ Προσθήκη Μαθητή"):
        with st.form("add_s"):
            n, ph, pr = st.text_input("Όνομα"), st.text_input("Τηλέφωνο"), st.number_input("Τιμή/ώρα", 0)
            if st.form_submit_button("Αποθήκευση"):
                st.session_state.df_s = pd.concat([st.session_state.df_s, pd.DataFrame([[n, ph, pr]], columns=st.session_state.df_s.columns)], ignore_index=True)
                save_all(); st.rerun()

    for i, r in st.session_state.df_s.iterrows():
        c1, c2, c3, c4, c5 = st.columns([2.5, 2, 1.5, 0.5, 0.5])
        c1.write(f"👤 {r['Όνομα']}"); c2.write(r['Τηλέφωνο']); c3.write(f"{r['Τιμή']}€/ώρα")
        if c4.button("✏️", key=f"e_{i}"): st.session_state.edit_idx = i; st.rerun()
        if c5.button("🗑️", key=f"d_{i}"):
            s_name = r['Όνομα']
            st.session_state.df_s = st.session_state.df_s.drop(i).reset_index(drop=True)
            st.session_state.df_l = st.session_state.df_l[st.session_state.df_l['Μαθητής'] != s_name].reset_index(drop=True)
            save_all(); st.rerun()
        if st.session_state.get('edit_idx') == i:
            with st.form(f"edit_{i}"):
                en, eph, epr = st.text_input("Όνομα", r['Όνομα']), st.text_input("Τηλέφωνο", r['Τηλέφωνο']), st.number_input("Τιμή", value=int(r['Τιμή']))
                if st.form_submit_button("Ενημέρωση"):
                    st.session_state.df_s.at[i, 'Όνομα'], st.session_state.df_s.at[i, 'Τηλέφωνο'], st.session_state.df_s.at[i, 'Τιμή'] = en, eph, epr
                    st.session_state.edit_idx = None; save_all(); st.rerun()

    st.divider()
    sel = st.selectbox("Προβολή καρτέλας:", ["-- Επιλογή --"] + st.session_state.df_s['Όνομα'].tolist())
    if sel != "-- Επιλογή --":
        t1, t2, t3 = st.tabs(["💰 Οικονομικά & SMS", "📝 Σημειώσεις & Αρχεία", "📜 Ιστορικό"])
        with t1:
            balance = st.session_state.df_l[(st.session_state.df_l['Μαθητής'] == sel) & (st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι")]['Ποσό'].sum()
            st.metric("Υπόλοιπο", f"{balance} €")
            if balance > 0:
                if st.button("Εξόφληση όλων"):
                    st.session_state.df_l.loc[(st.session_state.df_l['Μαθητής'] == sel) & (st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε"), 'Πληρώθηκε'] = "Ναι"; save_all(); st.rerun()
        with t2:
            with st.form("n_f", clear_on_submit=True):
                note, exam = st.text_area("Σημειώσεις"), st.text_input("Διαγώνισμα")
                up_f = st.file_uploader("Αρχείο")
                if st.form_submit_button("Αποθήκευση"):
                    fname = up_f.name if up_f else ""
                    if up_f: 
                        with open(os.path.join(UPLOAD_DIR, fname), "wb") as f: f.write(up_f.getbuffer())
                    d_n = datetime.now(ZoneInfo('Europe/Athens')).strftime('%d/%m/%Y')
                    new_n = pd.DataFrame([[sel, d_n, note, fname, exam]], columns=st.session_state.df_n.columns)
                    st.session_state.df_n = pd.concat([st.session_state.df_n, new_n], ignore_index=True); save_all(); st.rerun()
            for _, nr in st.session_state.df_n[st.session_state.df_n['Μαθητής'] == sel].iloc[::-1].iterrows():
                with st.expander(f"📅 {nr['Ημερομηνία']}"):
                    if nr['Σημειώσεις']: st.write(nr['Σημειώσεις'])
                    if nr['Διαγωνίσματα']: st.error(f"🚩 {nr['Διαγωνίσματα']}")
                    if nr['Αρχείο']: st.info(f"📂 Αρχείο: {nr['Αρχείο']}")
        with t3:
            hist = st.session_state.df_l[(st.session_state.df_l['Μαθητής'] == sel) & (st.session_state.df_l['Κατάσταση'] != "Προγραμματισμένο")]
            st.dataframe(hist.iloc[::-1], use_container_width=True)

# --- ΚΥΡΙΑ ΕΦΑΡΜΟΓΗ ---
def main():
    st.set_page_config(page_title="Teacher App Pro", layout="wide", page_icon="📚")
    if "auth" not in st.session_state: st.session_state.auth = False

    if not st.session_state.auth:
        st.title("📚 MyLessons")
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            tab_login, tab_signup = st.tabs(["🔑 Log in", "📝 Sign up"])
            with tab_login:
                u, p = st.text_input("Username"), st.text_input("Password", type="password")
                if st.button("Log in", use_container_width=True):
                    users = get_users(); row = users[users['username'] == u]
                    if not row.empty and row['password'].values[0] == hash_pw(p):
                        st.session_state.auth, st.session_state.user, st.session_state.cal_url = True, u, row['cal_url'].values[0]; st.rerun()
                    else: st.error("Λάθος στοιχεία!")
            with tab_signup:
                nu, np, nurl = st.text_input("Νέο User"), st.text_input("Νέο Pass", type="password"), st.text_input("iCloud Link")
                if st.button("Δημιουργία", use_container_width=True):
                    if save_user(nu, np, nurl): st.success("Έτοιμο!"); st.rerun()
        return

    # --- ΦΡΟΥΡΟΣ ΑΣΦΑΛΕΙΑΣ: Ελέγχει αν ο χρήστης υπάρχει ακόμα στο users.csv ---
    if st.session_state.user not in get_users()['username'].values:
        st.session_state.clear()
        st.rerun()
    # --------------------------------------------------------------------------

    load_data(st.session_state.user); auto_sync()
    st.sidebar.title(f"👤 {st.session_state.user}")
    menu = st.sidebar.radio("Μενού:", ["📊 Dashboard", "📅 Πρόγραμμα", "💰 Οικονομικά", "👥 Μαθητές", "⚙️ Ρυθμίσεις"])
    if st.sidebar.button("🚪 Log out"): st.session_state.clear(); st.rerun()

    if menu == "📊 Dashboard": show_dashboard()
    elif menu == "📅 Πρόγραμμα":
        st.header("📅 Πρόγραμμα")
        st.info("💡 **Σημείωση:** Χαζούλικο μου είπαμε να τα σβήνεις από το κινητό σου, μην ψάχνεις τον κάδο!!")
        pend = st.session_state.df_l[st.session_state.df_l['Κατάσταση'] == "Προγραμματισμένο"].copy()
        if not pend.empty:
            pend['temp_dt'] = pd.to_datetime(pend['Ημερομηνία'] + " " + pend['Ώρα'], format="%d/%m/%Y %H:%M", errors='coerce')
            pend = pend.sort_values('temp_dt').drop(columns=['temp_dt'])
        if pend.empty: st.success("Δεν υπάρχουν προγραμματισμένα μαθήματα.")
        for i, r in pend.iterrows():
            c1, c2, c3 = st.columns([3, 4, 2])
            c1.write(f"**{r['Μαθητής']}**"); c2.write(f"{r['Ημερομηνία']} | {r['Ώρα']} - {r['Λήξη']}")
            s_match = st.session_state.df_s[st.session_state.df_s['Όνομα'] == r['Μαθητής']]
            if not s_match.empty:
                msg = urllib.parse.quote(f"Υπενθυμίζω το αυριανό μας μάθημα στις {r['Ώρα']}. Καλή σας ημέρα!")
                c3.link_button("📱 SMS", f"sms:{s_match.iloc[0]['Τηλέφωνο']}?body={msg}")
    elif menu == "💰 Οικονομικά": show_finance_section()
    elif menu == "👥 Μαθητές": show_student_management()
    elif menu == "⚙️ Ρυθμίσεις":
        st.header("⚙️ Ρυθμίσεις")
        with st.form("set_f"):
            new_url = st.text_input("iCloud Link", value=st.session_state.cal_url)
            new_p1, new_p2 = st.text_input("Νέος Κωδικός", type="password"), st.text_input("Επιβεβαίωση", type="password")
            if st.form_submit_button("Αποθήκευση"):
                if new_p1 and new_p1 != new_p2: st.error("Λάθος κωδικός!")
                elif update_user_data(st.session_state.user, new_url, new_p1 if new_p1 else None):
                    st.session_state.cal_url = new_url; st.success("Αποθηκεύτηκε!"); st.rerun()

        st.divider()
        st.subheader("🚨 Διαγραφή Λογαριασμού")
        if st.checkbox("H διαγραφή είναι οριστική και τα δεδομένα μου θα χαθούν."):
            if st.button("🗑️ Οριστική Διαγραφή", type="primary"):
                delete_user_account(st.session_state.user)
                st.session_state.clear()
                st.rerun()

if __name__ == "__main__":
    main()
