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

# --- 1. ΒΟΗΘΗΤΙΚΕΣ ΣΥΝΑΡΤΗΣΕΙΣ & ΡΥΘΜΙΣΕΙΣ ---

def auto_collapse_sidebar():
    components.html(
        """
        <script>
        var button = window.parent.document.querySelector('button[aria-label="Close sidebar"]') || 
                     window.parent.document.querySelector('button[data-testid="sidebar-close-button"]') ||
                     window.parent.document.querySelector('button[kind="headerNoPadding"]');
        if (button) { setTimeout(function() { button.click(); }, 100); }
        </script>
        """,
        height=0,
    )

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def clean_currency(value):
    if value is None or value == "" or str(value).strip() == "#ERROR!": return 0.0
    s = str(value).replace('€', '').strip()
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        s = s.replace(',', '.')
    try:
        return float(s)
    except:
        return 0.0

UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

# --- 2. ΣΥΝΔΕΣΗ ΜΕ GOOGLE SHEETS ---

def get_gsheet_client():
    try:
        creds_dict = st.secrets["gcp_service_account"]
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        return client.open("mylessons")
    except Exception as e:
        st.error(f"Σφάλμα σύνδεσης: {e}")
        return None

# --- 3. ΔΙΑΧΕΙΡΙΣΗ ΧΡΗΣΤΩΝ ---

def get_users():
    sheet = get_gsheet_client()
    if not sheet: return pd.DataFrame(columns=["username", "password", "cal_url"])
    try:
        ws = sheet.worksheet("users")
        data = ws.get_all_records()
        return pd.DataFrame(data) if data else pd.DataFrame(columns=["username", "password", "cal_url"])
    except: return pd.DataFrame(columns=["username", "password", "cal_url"])

def update_user_data(username, new_url, new_pw=None):
    sheet = get_gsheet_client()
    if not sheet: return False
    ws = sheet.worksheet("users")
    df = get_users()
    if username in df['username'].values:
        idx = df[df['username'] == username].index[0] + 2
        ws.update_cell(idx, 3, new_url)
        if new_pw: ws.update_cell(idx, 2, hash_pw(new_pw))
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
            if not filtered.empty: ws.update([filtered.columns.values.tolist()] + filtered.values.tolist())

# --- 4. ΦΟΡΤΩΣΗ & ΑΠΟΘΗΚΕΥΣΗ ΔΕΔΟΜΕΝΩΝ ---

@st.cache_data(ttl=600)
def load_data_from_sheet(tab_name, username):
    sheet = get_gsheet_client()
    if not sheet: return pd.DataFrame()
    try:
        ws = sheet.worksheet(tab_name)
        df_all = pd.DataFrame(ws.get_all_records())
        if not df_all.empty and 'owner' in df_all.columns:
            df_filtered = df_all[df_all['owner'] == username].drop(columns=['owner']).reset_index(drop=True)
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
        if 'owner' not in mine.columns: mine.insert(0, 'owner', username)
        else: mine['owner'] = username
        final_df = pd.concat([others, mine], ignore_index=True).fillna("")
        ws.clear()
        ws.update([final_df.columns.values.tolist()] + final_df.values.tolist(), value_input_option='USER_ENTERED')
    except: pass

def load_data(username):
    if 'last_load' in st.session_state:
        if (datetime.now() - st.session_state.last_load).total_seconds() < 2: return
    
    df_s_raw = load_data_from_sheet("students", username)
    if df_s_raw.empty:
        st.session_state.df_s = pd.DataFrame(columns=["Όνομα", "Τηλέφωνο", "Τιμή"])
    else:
        df_s_raw['Τηλέφωνο'] = df_s_raw['Τηλέφωνο'].astype(str).replace('#ERROR!', '')
        df_s_raw['Τιμή'] = df_s_raw['Τιμή'].apply(clean_currency)
        st.session_state.df_s = df_s_raw
    
    df_l_raw = load_data_from_sheet("lessons", username)
    if df_l_raw.empty:
        st.session_state.df_l = pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Ώρα", "Λήξη", "Ποσό", "Κατάσταση", "Πληρώθηκε", "UID"])
    else:
        df_l_raw['Ποσό'] = df_l_raw['Ποσό'].apply(clean_currency)
        st.session_state.df_l = df_l_raw

    notes = load_data_from_sheet("notes", username)
    if notes.empty:
        st.session_state.df_n = pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Σημειώσεις", "Αρχείο", "Διαγωνίσματα"])
    else:
        st.session_state.df_n = notes
        today_str = datetime.now(ZoneInfo('Europe/Athens')).strftime('%Y-%m-%d')
        if 'Διαγωνίσματα' in st.session_state.df_n.columns:
            st.session_state.df_n['Διαγωνίσματα'] = st.session_state.df_n['Διαγωνίσματα'].apply(lambda x: x if str(x) >= today_str else "")

    st.session_state.last_load = datetime.now()

def save_all():
    user = st.session_state.user
    save_data_to_sheet(st.session_state.df_s, "students", user)
    save_data_to_sheet(st.session_state.df_l, "lessons", user)
    save_data_to_sheet(st.session_state.df_n, "notes", user)
    st.cache_data.clear()
    st.session_state.last_load = datetime.now()

# --- 5. ΣΥΓΧΡΟΝΙΣΜΟΣ ICLOUD ---

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
            if not st.session_state.df_l.empty and not st.session_state.df_l[st.session_state.df_l['UID'] == f"locked_{uid}"].empty:
                continue
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
                    price = round(float(((end - start).total_seconds() / 3600) * float(match['Τιμή'])), 2)
                    status = "Προγραμματισμένο" if now < end else "Ολοκληρώθηκε"
                    if st.session_state.df_l[st.session_state.df_l['UID'] == uid].empty:
                        new_lessons.append([match['Όνομα'], d_str, t_start, t_end, price, status, "Όχι", uid])
        if new_lessons:
            new_df = pd.DataFrame(new_lessons, columns=st.session_state.df_l.columns)
            st.session_state.df_l = pd.concat([st.session_state.df_l, new_df], ignore_index=True)
        save_all()
    except: pass

# --- 6. ΕΝΟΤΗΤΕΣ ΕΦΑΡΜΟΓΗΣ ---

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
        if st.button("🔄 Sync Now"): auto_sync(); st.rerun()
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
                        new_l = pd.DataFrame([[sel_m, d_m, ts, te, float(p_m), "Ολοκληρωθηκε", "Όχι", uid_m]], columns=st.session_state.df_l.columns)
                        st.session_state.df_l = pd.concat([st.session_state.df_l, new_l], ignore_index=True)
                        save_all(); st.rerun()
        st.divider()
        unpaid = st.session_state.df_l[(st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι")].copy()
        if unpaid.empty: st.success("Όλα εξοφλημένα!")
        else:
            unpaid['temp_dt'] = pd.to_datetime(unpaid['Ημερομηνία'] + " " + unpaid['Ώρα'], format="%d/%m/%Y %H:%M", errors='coerce')
            unpaid = unpaid.sort_values('temp_dt', ascending=False).drop(columns=['temp_dt'])
            for i, r in unpaid.iterrows():
                c1, c2, c3, c4 = st.columns([3, 1.5, 1.2, 1.5])
                try:
                    t1 = datetime.strptime(r['Ώρα'], '%H:%M')
                    t2 = datetime.strptime(r['Λήξη'], '%H:%M')
                    current_hours = (t2 - t1).seconds / 3600
                except: current_hours = 1.0
                
                c1.write(f"**{r['Μαθητής']}**\n{r['Ημερομηνία']} | {r['Ώρα']}-{r['Λήξη']}")
                
                if st.session_state.get(f"edit_{i}"):
                    new_h = c2.number_input("Ώρες", value=float(current_hours), step=0.25, key=f"h_{i}", label_visibility="collapsed")
                    if c2.button("💾", key=f"sv_{i}"):
                        s_price_row = st.session_state.df_s[st.session_state.df_s['Όνομα'] == r['Μαθητής']]
                        s_price = float(s_price_row['Τιμή'].values[0]) if not s_price_row.empty else 0.0
                        st.session_state.df_l.at[i, 'Λήξη'] = (t1 + timedelta(hours=new_h)).strftime('%H:%M')
                        st.session_state.df_l.at[i, 'Ποσό'] = round(new_h * s_price, 2)
                        if not str(st.session_state.df_l.at[i, 'UID']).startswith('locked_'):
                            st.session_state.df_l.at[i, 'UID'] = f"locked_{st.session_state.df_l.at[i, 'UID']}"
                        st.session_state[f"edit_{i}"] = False
                        save_all(); st.rerun()
                else:
                    col_price, col_edit = c2.columns([2, 1])
                    col_price.write(f"**{r['Ποσό']:.1f}€**")
                    if col_edit.button("✏️", key=f"ed_{i}"): st.session_state[f"edit_{i}"] = True; st.rerun()
                
                pay_val = c3.number_input("€", min_value=0.0, value=float(r['Ποσό']), key=f"p_{i}", format="%.2f", label_visibility="collapsed")
                
                b1, b2 = c4.columns(2)
                if b1.button("✔️", key=f"ok_{i}"):
                    diff = round(float(pay_val) - float(r['Ποσό']), 2)
                    st.session_state.df_l.at[i, 'Πληρώθηκε'] = "Ναι"
                    if diff != 0:
                        adj = pd.DataFrame([[r['Μαθητής'], r['Ημερομηνία'], "00:00", "00:00", -diff, "Ολοκληρώθηκε", "Όχι", f"adj_{datetime.now().timestamp()}"]], columns=st.session_state.df_l.columns)
                        st.session_state.df_l = pd.concat([st.session_state.df_l, adj], ignore_index=True)
                    save_all(); st.rerun()
                if b2.button("✖️", key=f"no_{i}", help="Δεν πραγματοποιήθηκε"):
                    st.session_state.df_l = st.session_state.df_l.drop(i).reset_index(drop=True)
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
            
            if not df_f.empty:
                all_students_in_month = sorted(df_f['Μαθητής'].unique().tolist())
                selected_students = st.multiselect("Επιλογή Μαθητών για Σύνολο", all_students_in_month, default=all_students_in_month)
                
                calculated_revenue = df_f[df_f['Μαθητής'].isin(selected_students)]['Ποσό'].sum()
                st.metric("💶 Συνολικά Έσοδα Μήνα (Επιλεγμένα)", f"{calculated_revenue:.2f} €")
                
                st.divider()
                summary = df_f.groupby('Μαθητής').agg({'Ποσό': 'sum', 'Ημερομηνία': 'count'}).reset_index()
                for _, row in summary.iterrows():
                    with st.expander(f"{row['Μαθητής']} | Σύνολο: {row['Ποσό']:.2f} €"):
                        s_info = st.session_state.df_s[st.session_state.df_s['Όνομα'] == row['Μαθητής']]
                        if not s_info.empty:
                            txt = urllib.parse.quote(f"Καλησπέρα σας, αυτόν τον μήνα έχουν γίνει {row['Ημερομηνία']} μαθήματα. Το υπόλοιπο είναι {df_f[(df_f['Μαθητής'] == row['Μαθητής']) & (df_f['Πληρώθηκε'] == 'Όχι')]['Ποσό'].sum():.2f}€.")
                            st.link_button(f"📱 Αποστολή SMS", f"sms:{s_info.iloc[0]['Τηλέφωνο']}?body={txt}")
                        for _, det in df_f[df_f['Μαθητής'] == row['Μαθητής']].iterrows():
                            st.write(f"{'✅' if det['Πληρώθηκε']=='Ναι' else '⏳'} {det['Ημερομηνία']}: {det['Ποσό']:.2f}€")

def show_student_management():
    if 'view_mode' not in st.session_state: st.session_state.view_mode = 'list'
    if st.session_state.view_mode == 'list':
        st.header("👥 Διαχείριση Μαθητών")
        with st.expander("➕ Προσθήκη Μαθητή"):
            with st.form("add_s_new"):
                n, ph, pr = st.text_input("Όνομα"), st.text_input("Τηλέφωνο"), st.number_input("Τιμή/ώρα", 0.0, format="%.2f")
                if st.form_submit_button("Αποθήκευση"):
                    st.session_state.df_s = pd.concat([st.session_state.df_s, pd.DataFrame([[n, str(ph), pr]], columns=st.session_state.df_s.columns)], ignore_index=True)
                    save_all(); st.rerun()
        st.write("---")
        for i, r in st.session_state.df_s.iterrows():
            c1, c2, c3, c4, c5 = st.columns([2.5, 2, 1.5, 0.5, 0.5])
            if c1.button(f"👤 {r['Όνομα']}", key=f"btn_{i}"): st.session_state.selected_student = r['Όνομα']; st.session_state.view_mode = 'card'; st.rerun()
            if st.session_state.get(f"edit_student_{i}"):
                with st.form(f"edit_s_form_{i}"):
                    new_n, new_ph, new_pr = st.text_input("Όνομα", value=r['Όνομα']), st.text_input("Τηλέφωνο", value=r['Τηλέφωνο']), st.number_input("Τιμή/ώρα", value=float(r['Τιμή']), format="%.2f")
                    if st.form_submit_button("💾"):
                        st.session_state.df_s.at[i, 'Όνομα'], st.session_state.df_s.at[i, 'Τηλέφωνο'], st.session_state.df_s.at[i, 'Τιμή'] = new_n, str(new_ph), new_pr
                        st.session_state[f"edit_student_{i}"] = False; save_all(); st.rerun()
            else:
                c2.write(r['Τηλέφωνο'])
                c3.write(f"{r['Τιμή']:.2f} €/ώρα")
                if c4.button("✏️", key=f"ed_s_{i}"): st.session_state[f"edit_student_{i}"] = True; st.rerun()
                if c5.button("🗑️", key=f"del_{i}"): st.session_state.df_s = st.session_state.df_s.drop(i).reset_index(drop=True); save_all(); st.rerun()
    
    elif st.session_state.view_mode == 'card':
        sel = st.session_state.selected_student
        st.header(f"📂 Καρτέλα: {sel}")
        if st.button("⬅️ Πίσω"): st.session_state.view_mode = 'list'; st.rerun()
        t1, t2, t3 = st.tabs(["💰 Οικονομικά", "📝 Σημειώσεις", "📜 Ιστορικό"])
        
        with t1:
            unpaid_df = st.session_state.df_l[(st.session_state.df_l['Μαθητής'] == sel) & (st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι")]
            st.metric("Ανεξόφλητο Υπόλοιπο", f"{unpaid_df['Ποσό'].sum():.2f} €")
            if st.button("Εξόφληση Όλων"):
                st.session_state.df_l.loc[(st.session_state.df_l['Μαθητής'] == sel) & (st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε"), 'Πληρώθηκε'] = "Ναι"
                save_all(); st.rerun()
        
        with t2:
            with st.form("note_page", clear_on_submit=True):
                nt = st.text_area("Σημειώσεις Μαθήματος")
                ex_date = st.date_input("Προγραμματισμός Διαγωνίσματος", value=None, format="DD/MM/YYYY")
                uploaded_file = st.file_uploader("Αρχείο/Φωτογραφία")
                manual_link = st.text_input("Link")
                if st.form_submit_button("Αποθήκευση"):
                    f_link = manual_link
                    if uploaded_file:
                        f_link = os.path.join(UPLOAD_DIR, uploaded_file.name)
                        with open(f_link, "wb") as f: f.write(uploaded_file.getbuffer())
                    new_n = pd.DataFrame([[sel, datetime.now().strftime('%d/%m/%Y'), nt, f_link, ex_date.strftime('%Y-%m-%d') if ex_date else ""]], columns=st.session_state.df_n.columns)
                    st.session_state.df_n = pd.concat([st.session_state.df_n, new_n], ignore_index=True); save_all(); st.rerun()
            
            st.subheader("Ιστορικό Σημειώσεων")
            student_notes = st.session_state.df_n[st.session_state.df_n['Μαθητής'] == sel].iloc[::-1]
            if student_notes.empty:
                st.info("Δεν υπάρχουν σημειώσεις.")
            else:
                for idx, nr in student_notes.iterrows():
                    with st.container(border=True):
                        c1, c2 = st.columns([0.85, 0.15])
                        c1.markdown(f"**📅 {nr['Ημερομηνία']}**")
                        if nr['Διαγωνίσματα'] and nr['Διαγωνίσματα'] != "":
                            try:
                                formatted_exam = datetime.strptime(nr['Διαγωνίσματα'], '%Y-%m-%d').strftime('%d/%m/%Y')
                                c1.error(f"🚨 Διαγώνισμα: {formatted_exam}")
                            except: pass
                        st.write(nr['Σημειώσεις'])
                        if nr['Αρχείο']: st.link_button("📂 Αρχείο", nr['Αρχείο'])
                        if c2.button("🗑️", key=f"dn_{idx}"):
                            st.session_state.df_n = st.session_state.df_n.drop(idx).reset_index(drop=True)
                            save_all(); st.rerun()

        with t3:
            hist = st.session_state.df_l[
                (st.session_state.df_l['Μαθητής'] == sel) & 
                (st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε")
            ].copy()
            if hist.empty:
                st.info("Δεν υπάρχουν ολοκληρωμένα μαθήματα.")
            else:
                hist['temp_dt'] = pd.to_datetime(hist['Ημερομηνία'], format="%d/%m/%Y", errors='coerce')
                hist = hist.sort_values('temp_dt', ascending=False).drop(columns=['temp_dt'])
                for idx, hr in hist.iterrows():
                    hc1, hc2 = st.columns([9, 1])
                    icon = "✅" if hr['Πληρώθηκε'] == "Ναι" else "⏳"
                    hc1.write(f"{icon} {hr['Ημερομηνία']} | {hr['Ώρα']} - {hr['Λήξη']} | {hr['Ποσό']:.2f} €")
                    if hc2.button("🗑️", key=f"del_hist_{idx}"):
                        st.session_state.df_l = st.session_state.df_l.drop(idx).reset_index(drop=True)
                        save_all(); st.rerun()

def show_settings():
    st.header("⚙️ Ρυθμίσεις")
    with st.expander("🔗 iCloud & Password"):
        with st.form("update_u"):
            n_url, n_pw = st.text_input("iCloud Link", value=st.session_state.cal_url), st.text_input("Νέος Κωδικός", type="password")
            if st.form_submit_button("Αποθήκευση"):
                if update_user_data(st.session_state.user, n_url, n_pw if n_pw else None):
                    st.session_state.cal_url = n_url; st.success("Ενημερώθηκε!")
                else: st.error("Σφάλμα.")
    if st.button("🔴 Διαγραφή Λογαριασμού", type="primary"): delete_user_account(st.session_state.user); st.session_state.clear(); st.rerun()

# --- 7. ΚΥΡΙΑ ΡΟΗ ΕΦΑΡΜΟΓΗΣ ---

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
                st.session_state.auth, st.session_state.user, st.session_state.cal_url = True, u, row['cal_url'].values[0]
                load_data(u); st.rerun()
            else: st.error("Λάθος στοιχεία!")
        return

    load_data(st.session_state.user)
    if "menu_option" not in st.session_state: st.session_state.menu_option = "📊 Dashboard"
    menu = st.sidebar.radio("Μενού:", ["📊 Dashboard", "📅 Πρόγραμμα", "💰 Οικονομικά", "👥 Μαθητές", "⚙️ Ρυθμίσεις"])
    if menu != st.session_state.menu_option: st.session_state.menu_option = menu; auto_collapse_sidebar(); st.rerun()
    if st.sidebar.button("🚪 Log out"): st.session_state.clear(); st.rerun()

    if menu == "📊 Dashboard": show_dashboard()
    elif menu == "📅 Πρόγραμμα":
        st.header("📅 Πρόγραμμα")
        pend = st.session_state.df_l[st.session_state.df_l['Κατάσταση'] == "Προγραμματισμένο"].copy()
        if pend.empty: st.success("Κανένα μάθημα.")
        else:
            pend['temp_sort_dt'] = pd.to_datetime(pend['Ημερομηνία'] + " " + pend['Ώρα'], format="%d/%m/%Y %H:%M", errors='coerce')
            pend = pend.sort_values('temp_sort_dt', ascending=True).drop(columns=['temp_sort_dt'])
            for i, r in pend.iterrows():
                c1, c2, c3 = st.columns([3, 4, 2])
                c1.write(f"**{r['Μαθητής']}**")
                c2.write(f"{r['Ημερομηνία']} | {r['Ώρα']}")
                s_match = st.session_state.df_s[st.session_state.df_s['Όνομα'] == r['Μαθητής']]
                if not s_match.empty: c3.link_button("📱 SMS", f"sms:{s_match.iloc[0]['Τηλέφωνο']}?body={urllib.parse.quote('Υπενθύμιση μαθήματος.')}")
    elif menu == "💰 Οικονομικά": show_finance_section()
    elif menu == "👥 Μαθητές": show_student_management()
    elif menu == "⚙️ Ρυθμίσεις": show_settings()

if __name__ == "__main__": main()
