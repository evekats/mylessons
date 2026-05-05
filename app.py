import streamlit as st
import pandas as pd
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

# --- ΑΠΟΘΗΚΕΥΣΗ ΣΕ EXCEL ΑΝΤΙ ΓΙΑ GOOGLE SHEETS ---
DB_FILE = "lessons_data.xlsx"

def load_excel_tab(tab_name):
    if not os.path.exists(DB_FILE):
        return pd.DataFrame()
    try:
        with pd.ExcelFile(DB_FILE) as xls:
            if tab_name in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=tab_name)
                # Καθαρισμός τηλεφώνων κατά τη φόρτωση
                if 'Τηλέφωνο' in df.columns:
                    df['Τηλέφωνο'] = df['Τηλέφωνο'].astype(str).replace(r'\.0$', '', regex=True).replace('nan', '')
                return df
            return pd.DataFrame()
    except Exception:
        return pd.DataFrame()

def save_excel_tab(df, tab_name):
    # Διασφάλιση σωστού τύπου δεδομένων πριν την αποθήκευση
    if 'Τηλέφωνο' in df.columns:
        df['Τηλέφωνο'] = df['Τηλέφωνο'].astype(str).replace(r'\.0$', '', regex=True).replace('nan', '')
    
    if not os.path.exists(DB_FILE):
        with pd.ExcelWriter(DB_FILE, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name=tab_name, index=False)
        return

    with pd.ExcelWriter(DB_FILE, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
        df.to_excel(writer, sheet_name=tab_name, index=False)

# --- Φάκελος για Uploads ---
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

# --- ΣΥΝΑΡΤΗΣΕΙΣ ΑΣΦΑΛΕΙΑΣ & ΔΙΑΧΕΙΡΙΣΗΣ ΧΡΗΣΤΩΝ ---
def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def get_users():
    df = load_excel_tab("users")
    if df.empty:
        return pd.DataFrame(columns=["username", "password", "cal_url"])
    return df

def save_user(username, password, cal_url):
    df = get_users()
    if username in df['username'].values:
        return False
    new_user = pd.DataFrame([[username, hash_pw(password), cal_url]], columns=df.columns)
    df = pd.concat([df, new_user], ignore_index=True)
    save_excel_tab(df, "users")
    return True

def update_user_data(username, new_url, new_pw=None):
    df = get_users()
    if username in df['username'].values:
        idx = df[df['username'] == username].index[0]
        df.at[idx, 'cal_url'] = new_url
        if new_pw:
            df.at[idx, 'password'] = hash_pw(new_pw)
        save_excel_tab(df, "users")
        return True
    return False

def delete_user_account(username):
    users = get_users()
    if username in users['username'].values:
        users = users[users['username'] != username]
        save_excel_tab(users, "users")
    
    for tab in ["students", "lessons", "notes"]:
        data = load_excel_tab(tab)
        if not data.empty and 'owner' in data.columns:
            filtered = data[data['owner'] != username]
            save_excel_tab(filtered, tab)

# --- ΦΟΡΤΩΣΗ & ΑΠΟΘΗΚΕΥΣΗ ΜΕ ΔΙΟΡΘΩΣΗ ΔΕΚΑΔΙΚΩΝ ---
def load_data(username):
    if 'last_load' in st.session_state:
        if (datetime.now() - st.session_state.last_load).total_seconds() < 2:
            return

    # Φόρτωση μαθητών
    df_s_all = load_excel_tab("students")
    if not df_s_all.empty and 'owner' in df_s_all.columns:
        st.session_state.df_s = df_s_all[df_s_all['owner'] == username].drop(columns=['owner']).reset_index(drop=True)
    else:
        st.session_state.df_s = pd.DataFrame(columns=["Όνομα", "Τηλέφωνο", "Τιμή"])

    # Φόρτωση μαθημάτων
    df_l_all = load_excel_tab("lessons")
    if not df_l_all.empty and 'owner' in df_l_all.columns:
        df_l_raw = df_l_all[df_l_all['owner'] == username].drop(columns=['owner']).reset_index(drop=True)
        df_l_raw['Ποσό'] = pd.to_numeric(df_l_raw['Ποσό'], errors='coerce').fillna(0.0).astype(float)
        st.session_state.df_l = df_l_raw
    else:
        st.session_state.df_l = pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Ώρα", "Λήξη", "Ποσό", "Κατάσταση", "Πληρώθηκε", "UID"])

    # Φόρτωση σημειώσεων
    df_n_all = load_excel_tab("notes")
    if not df_n_all.empty and 'owner' in df_n_all.columns:
        notes = df_n_all[df_n_all['owner'] == username].drop(columns=['owner']).reset_index(drop=True)
        today_str = datetime.now(ZoneInfo('Europe/Athens')).strftime('%Y-%m-%d')
        notes['Διαγωνίσματα'] = notes['Διαγωνίσματα'].apply(lambda x: x if str(x) >= today_str else "")
        st.session_state.df_n = notes
    else:
        st.session_state.df_n = pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Σημειώσεις", "Αρχείο", "Διαγωνίσματα"])
        
    st.session_state.last_load = datetime.now()

def save_all():
    user = st.session_state.user
    
    for tab, state_key in [("students", "df_s"), ("lessons", "df_l"), ("notes", "df_n")]:
        current_data = st.session_state[state_key].copy()
        all_data = load_excel_tab(tab)
        
        others = all_data[all_data['owner'] != user] if not all_data.empty and 'owner' in all_data.columns else pd.DataFrame()
        current_data.insert(0, 'owner', user)
        
        final_df = pd.concat([others, current_data], ignore_index=True)
        save_excel_tab(final_df, tab)
    
    st.session_state.last_load = datetime.now()

def auto_sync():
    cal_url = st.session_state.cal_url
    if not cal_url or str(cal_url) == "nan": return
    try:
        res = requests.get(cal_url, timeout=5)
        gcal = Calendar.from_ical(res.content)
        gr_tz = ZoneInfo('Europe/Athens')
        now = datetime.now(gr_tz).replace(tzinfo=None)

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
                    hourly_rate = float(match['Τιμή'])
                    duration = (end - start).total_seconds() / 3600
                    price = round(duration * hourly_rate, 2)
                    
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

# --- UI SECTIONS (ΟΛΕΣ ΟΙ ΛΕΙΤΟΥΡΓΙΕΣ ΠΑΡΑΜΕΝΟΥΝ ΙΔΙΕΣ) ---
def show_dashboard():
    st.header("📊 Dashboard")
    col_m1, col_m2, col_m3 = st.columns(3)
    gr_tz = ZoneInfo('Europe/Athens')
    today = datetime.now(gr_tz).strftime('%d/%m/%Y')
    lessons_today = len(st.session_state.df_l[st.session_state.df_l['Ημερομηνία'] == today])
    unpaid_total = st.session_state.df_l[(st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι")]['Ποσό'].sum()
    col_m1.metric("Σημερινά Μαθήματα", lessons_today)
    col_m2.metric("Εκκρεμείς Πληρωμές", f"{unpaid_total:.2f} €")
    col_m3.metric("Σύνολο Μαθητών", len(st.session_state.df_s))
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("☁️ iCloud Sync")
        if st.button("🔄 Sync Now"): 
            auto_sync()
            st.rerun()
    with c2:
        st.subheader("📅 Διαγωνίσματα")
        exams = st.session_state.df_n[st.session_state.df_n['Διαγωνίσματα'].notna() & (st.session_state.df_n['Διαγωνίσματα'] != "")]
        if not exams.empty:
            for _, r in exams.iterrows():
                try:
                    d_obj = datetime.strptime(r['Διαγωνίσματα'], '%Y-%m-%d')
                    st.warning(f"**{r['Μαθητής']}**: {d_obj.strftime('%d/%m/%Y')}")
                except: st.warning(f"**{r['Μαθητής']}**: {r['Διαγωνίσματα']}")
        else: st.info("Κανένα διαγώνισμα.")

def show_finance_section():
    st.header("💰 Οικονομικά")
    tab_p, tab_r = st.tabs(["💵 Πληρωμές", "📈 Μηνιαία Αναφορά"])
    with tab_p:
        if not st.session_state.df_s.empty:
            with st.expander("➕ Προσθήκη Μαθήματος"):
                with st.form("manual_lesson_form"):
                    c1, c2, c3, c4 = st.columns(4)
                    sel_m = c1.selectbox("Μαθητής", st.session_state.df_s['Όνομα'].tolist())
                    d_m = c2.text_input("Ημερομηνία", datetime.now(ZoneInfo('Europe/Athens')).strftime("%d/%m/%Y"))
                    t_m = c3.text_input("Ώρα (π.χ. 16:00-17:00)", "16:00 - 17:00")
                    p_m = c4.number_input("Ποσό (€)", min_value=0.0, step=0.1, format="%.2f")
                    if st.form_submit_button("Προσθήκη"):
                        uid_m = f"manual_{datetime.now().timestamp()}"
                        ts, te = (t_m.split("-")[0].strip(), t_m.split("-")[1].strip()) if "-" in t_m else (t_m, t_m)
                        new_l = pd.DataFrame([[sel_m, d_m, ts, te, float(p_m), "Ολοκληρώθηκε", "Όχι", uid_m]], columns=st.session_state.df_l.columns)
                        st.session_state.df_l = pd.concat([st.session_state.df_l, new_l], ignore_index=True)
                        save_all(); st.rerun()
        st.divider()
        st.session_state.df_l['Ποσό'] = pd.to_numeric(st.session_state.df_l['Ποσό'], errors='coerce').fillna(0.0)
        unpaid = st.session_state.df_l[(st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι") & (st.session_state.df_l['Ποσό'] > 0)].copy()
        if unpaid.empty: st.success("Όλα εξοφλημένα!")
        else:
            unpaid['temp_dt'] = pd.to_datetime(unpaid['Ημερομηνία'] + " " + unpaid['Ώρα'], format="%d/%m/%Y %H:%M", errors='coerce')
            unpaid = unpaid.sort_values('temp_dt', ascending=False).drop(columns=['temp_dt'])
            for i, r in unpaid.iterrows():
                c1, c2, c3, c4 = st.columns([3, 1.5, 1.5, 2.5])
                c1.write(f"**{r['Μαθητής']}**\n{r['Ημερομηνία']} | {r['Ώρα']}-{r['Λήξη']}")
                if st.session_state.get(f"edit_{i}"):
                    new_amt = c2.number_input("Νέο Ποσό", value=float(r['Ποσό']), step=0.1, format="%.2f", key=f"new_{i}")
                    if c2.button("💾", key=f"sv_{i}"):
                        st.session_state.df_l.at[i, 'Ποσό'] = float(new_amt)
                        st.session_state[f"edit_{i}"] = False; save_all(); st.rerun()
                else:
                    c2.write(f"**{r['Ποσό']:.2f}€**")
                    if c2.button("✏️", key=f"ed_{i}"): st.session_state[f"edit_{i}"] = True; st.rerun()
                pay_val = c3.number_input("Πληρωμή", min_value=0.0, value=float(r['Ποσό']), step=0.1, format="%.2f", key=f"pay_{i}")
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
                    with st.expander(f"{row['Μαθητής']} | Σύνολο: {row['Ποσό']:.2f}€"):
                        s_info = st.session_state.df_s[st.session_state.df_s['Όνομα'] == row['Μαθητής']]
                        if not s_info.empty:
                            total_month = row['Ημερομηνία']
                            paid_month = len(df_f[(df_f['Μαθητής'] == row['Μαθητής']) & (df_f['Πληρώθηκε'] == 'Ναι')])
                            unpaid_amt = df_f[(df_f['Μαθητής'] == row['Μαθητής']) & (df_f['Πληρώθηκε'] == 'Όχι')]['Ποσό'].sum()
                            sms_text = (f"Καλησπέρα σας, αυτόν τον μήνα έχουν γίνει συνολικά {total_month} μαθήματα, "
                                        f"εκ των οποίων έχουν πληρωθεί τα {paid_month}. "
                                        f"Το υπόλοιπο ποσό είναι {unpaid_amt:.2f}€.")
                            txt_encoded = urllib.parse.quote(sms_text)
                            st.link_button(f"📱 Αποστολή Αναφοράς SMS", f"sms:{s_info.iloc[0]['Τηλέφωνο']}?body={txt_encoded}")
                        for _, det in df_f[df_f['Μαθητής'] == row['Μαθητής']].iterrows():
                            st.write(f"{'✅' if det['Πληρώθηκε']=='Ναι' else '⏳'} {det['Ημερομηνία']} ({det['Ώρα']}-{det['Λήξη']}): {det['Ποσό']:.2f}€")

def show_student_management():
    if 'view_mode' not in st.session_state: st.session_state.view_mode = 'list'
    if st.session_state.view_mode == 'list':
        st.header("👥 Διαχείριση Μαθητών")
        with st.expander("➕ Προσθήκη Μαθητή"):
            with st.form("add_s_new"):
                n, ph, pr = st.text_input("Όνομα"), st.text_input("Τηλέφωνο"), st.number_input("Τιμή/ώρα", 15.0)
                if st.form_submit_button("Αποθήκευση"):
                    ph_clean = ph.split('.')[0] if '.' in ph else ph
                    st.session_state.df_s = pd.concat([st.session_state.df_s, pd.DataFrame([[n, ph_clean, pr]], columns=st.session_state.df_s.columns)], ignore_index=True)
                    save_all(); st.rerun()
        st.divider()
        for i, r in st.session_state.df_s.iterrows():
            c1, c2, c3, c4, c5 = st.columns([2.5, 2, 1.5, 0.5, 0.5])
            if c1.button(f"👤 {r['Όνομα']}", key=f"btn_{i}"):
                st.session_state.selected_student = r['Όνομα']
                st.session_state.view_mode = 'card'; st.rerun()
            if st.session_state.get(f"edit_student_{i}"):
                with st.form(f"edit_s_form_{i}"):
                    new_n = st.text_input("Όνομα", value=r['Όνομα'])
                    new_ph = st.text_input("Τηλέφωνο", value=str(r['Τηλέφωνο']))
                    new_pr = st.number_input("Τιμή/ώρα", value=float(r['Τιμή']))
                    if st.form_submit_button("💾"):
                        st.session_state.df_s.at[i, 'Όνομα'] = new_n
                        st.session_state.df_s.at[i, 'Τηλέφωνο'] = new_ph.split('.')[0]
                        st.session_state.df_s.at[i, 'Τιμή'] = new_pr
                        st.session_state[f"edit_student_{i}"] = False
                        save_all(); st.rerun()
            else:
                c2.write(str(r['Τηλέφωνο']))
                c3.write(f"{r['Τιμή']}€/ώρα")
                if c4.button("✏️", key=f"ed_s_{i}"): st.session_state[f"edit_student_{i}"] = True; st.rerun()
                if c5.button("🗑️", key=f"del_{i}"):
                    st.session_state.df_s = st.session_state.df_s.drop(i).reset_index(drop=True)
                    save_all(); st.rerun()

    elif st.session_state.view_mode == 'card':
        sel = st.session_state.selected_student
        c_title, c_back = st.columns([0.8, 0.2])
        c_title.header(f"📂 Καρτέλα: {sel}")
        if c_back.button("⬅️ Πίσω"):
            st.session_state.view_mode = 'list'; st.rerun()
        st.divider()
        t1, t2, t3 = st.tabs(["💰 Οικονομικά", "📝 Σημειώσεις", "📜 Ιστορικό"])
        with t1:
            unpaid_df = st.session_state.df_l[(st.session_state.df_l['Μαθητής'] == sel) & (st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι")]
            balance = unpaid_df['Ποσό'].sum()
            st.metric("Ανεξόφλητο Υπόλοιπο", f"{balance:.2f} €")
            if balance > 0 and st.button(f"Εξόφληση Όλων"):
                st.session_state.df_l.loc[(st.session_state.df_l['Μαθητής'] == sel) & (st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε"), 'Πληρώθηκε'] = "Ναι"
                save_all(); st.rerun()
        with t2:
            with st.form("note_page", clear_on_submit=True):
                nt = st.text_area("Σημειώσεις")
                ex_date = st.date_input("Ημερομηνία Διαγωνίσματος", value=None, format="DD/MM/YYYY")
                uploaded_file = st.file_uploader("Αρχείο", type=["pdf", "png", "jpg", "docx"])
                manual_link = st.text_input("Link Αρχείου")
                if st.form_submit_button("Αποθήκευση"):
                    final_link = manual_link
                    if uploaded_file:
                        file_path = os.path.join(UPLOAD_DIR, uploaded_file.name)
                        with open(file_path, "wb") as f: f.write(uploaded_file.getbuffer())
                        final_link = file_path
                    d = datetime.now(ZoneInfo('Europe/Athens')).strftime('%d/%m/%Y')
                    ex_val = ex_date.strftime('%Y-%m-%d') if ex_date else ""
                    new_n = pd.DataFrame([[sel, d, nt, final_link, ex_val]], columns=st.session_state.df_n.columns)
                    st.session_state.df_n = pd.concat([st.session_state.df_n, new_n], ignore_index=True)
                    save_all(); st.rerun()
            for idx, nr in st.session_state.df_n[st.session_state.df_n['Μαθητής'] == sel].iloc[::-1].iterrows():
                with st.expander(f"📅 {nr['Ημερομηνία']}"):
                    st.write(nr['Σημειώσεις'])
                    if st.button("🗑️ Σημείωση", key=f"dn_{idx}"):
                        st.session_state.df_n = st.session_state.df_n.drop(idx).reset_index(drop=True)
                        save_all(); st.rerun()
        with t3:
            hist = st.session_state.df_l[st.session_state.df_l['Μαθητής'] == sel]
            st.dataframe(hist.iloc[::-1].drop(columns=['UID'], errors='ignore'), use_container_width=True)

def show_settings():
    st.header("⚙️ Ρυθμίσεις")
    with st.expander("🔗 iCloud Link & Password"):
        with st.form("update_u"):
            new_url = st.text_input("iCloud Link", value=st.session_state.cal_url)
            new_pw = st.text_input("Νέος Κωδικός", type="password")
            if st.form_submit_button("Αποθήκευση"):
                if update_user_data(st.session_state.user, new_url, new_pw if new_pw else None):
                    st.session_state.cal_url = new_url; st.success("Ενημερώθηκε!")
                else: st.error("Σφάλμα.")
    if st.button("🔴 Διαγραφή Λογαριασμού", type="primary"):
        delete_user_account(st.session_state.user)
        st.session_state.clear(); st.rerun()

def main():
    st.set_page_config(page_title="MyLessons Pro", layout="wide", page_icon="📚", initial_sidebar_state="collapsed")
    if "auth" not in st.session_state: st.session_state.auth = False
    
    if not st.session_state.auth:
        st.title("📚 MyLessons")
        u, p = st.text_input("Username"), st.text_input("Password", type="password")
        if st.button("Log in", use_container_width=True):
            users = get_users()
            row = users[users['username'] == u]
            if not row.empty and row['password'].values[0] == hash_pw(p):
                st.session_state.auth, st.session_state.user = True, u
                st.session_state.cal_url = row['cal_url'].values[0]
                load_data(u); st.rerun()
            else: st.error("Λάθος στοιχεία!")
        return

    load_data(st.session_state.user)
    menu = st.sidebar.radio("Μενού:", ["📊 Dashboard", "📅 Πρόγραμμα", "💰 Οικονομικά", "👥 Μαθητές", "⚙️ Ρυθμίσεις"])
    if st.sidebar.button("🚪 Log out"): st.session_state.clear(); st.rerun()

    if menu == "📊 Dashboard": show_dashboard()
    elif menu == "📅 Πρόγραμμα":
        st.header("📅 Πρόγραμμα")
        pend = st.session_state.df_l[st.session_state.df_l['Κατάσταση'] == "Προγραμματισμένο"].copy()
        if not pend.empty:
            pend['temp_dt'] = pd.to_datetime(pend['Ημερομηνία'] + " " + pend['Ώρα'], format="%d/%m/%Y %H:%M", errors='coerce')
            for i, r in pend.sort_values('temp_dt').iterrows():
                c1, c2, c3 = st.columns([3, 4, 2])
                c1.write(f"**{r['Μαθητής']}**")
                c2.write(f"{r['Ημερομηνία']} | {r['Ώρα']}")
                s_match = st.session_state.df_s[st.session_state.df_s['Όνομα'] == r['Μαθητής']]
                if not s_match.empty:
                    msg = urllib.parse.quote(f"Υπενθυμίζω το μάθημα μας στις {r['Ώρα']}.")
                    c3.link_button("📱 SMS", f"sms:{s_match.iloc[0]['Τηλέφωνο']}?body={msg}")
        else: st.success("Κανένα προγραμματισμένο μάθημα.")
    elif menu == "💰 Οικονομικά": show_finance_section()
    elif menu == "👥 Μαθητές": show_student_management()
    elif menu == "⚙️ Ρυθμίσεις": show_settings()

if __name__ == "__main__":
    main()
