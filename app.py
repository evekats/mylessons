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

# --- 1. ΒΕΛΤΙΩΜΕΝΗ ΛΕΙΤΟΥΡΓΙΑ ΓΙΑ ΑΥΤΟΜΑΤΟ ΚΛΕΙΣΙΜΟ SIDEBAR ---
def auto_collapse_sidebar():
    components.html(
        """
        <script>
        // Ψάχνουμε το κουμπί που ελέγχει το sidebar στο header
        var button = window.parent.document.querySelector('button[aria-label="Close sidebar"]') || 
                     window.parent.document.querySelector('button[data-testid="sidebar-close-button"]') ||
                     window.parent.document.querySelector('button[kind="headerNoPadding"]');
        
        if (button) {
            setTimeout(function() {
                button.click();
            }, 100);
        }
        </script>
        """,
        height=0,
    )

# --- Φάκελος για Uploads ---
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

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

# --- ΦΟΡΤΩΣΗ & ΑΠΟΘΗΚΕΥΣΗ ---
@st.cache_data(ttl=600)
def load_data_from_sheet(tab_name, username):
    sheet = get_gsheet_client()
    if not sheet: return pd.DataFrame()
    try:
        ws = sheet.worksheet(tab_name)
        df_all = pd.DataFrame(ws.get_all_records())
        if not df_all.empty and 'owner' in df_all.columns:
            df_filtered = df_all[df_all['owner'] == username].drop(columns=['owner']).reset_index(drop=True)
            if 'Ποσό' in df_filtered.columns:
                df_filtered['Ποσό'] = pd.to_numeric(df_filtered['Ποσό'].astype(str).str.replace(',', '.'), errors='coerce').fillna(0.0)
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
        
        # ΕΔΩ ΓΙΝΕΤΑΙ Η ΔΙΟΡΘΩΣΗ: Μετατροπή σε float πριν το save
        if 'Ποσό' in mine.columns:
            mine['Ποσό'] = pd.to_numeric(mine['Ποσό'].astype(str).str.replace(',', '.'), errors='coerce').fillna(0.0)
            
        final_df = pd.concat([others, mine], ignore_index=True).fillna("")
        ws.clear()
        # Χρήση USER_ENTERED για να αναγνωρίσει το Sheets τους δεκαδικούς αριθμούς
        ws.update([final_df.columns.values.tolist()] + final_df.values.tolist(), value_input_option='USER_ENTERED')
    except: pass

def load_data(username):
    # Αποφυγή συνεχόμενων φορτώσεων
    if 'last_load' in st.session_state:
        if (datetime.now() - st.session_state.last_load).total_seconds() < 2:
            return
            
    # 1. Φόρτωση Μαθητών
    st.session_state.df_s = load_data_from_sheet("students", username)
    if st.session_state.df_s.empty: 
        st.session_state.df_s = pd.DataFrame(columns=["Όνομα", "Τηλέφωνο", "Τιμή"])
    
    # 2. Φόρτωση Μαθημάτων
    df_l_raw = load_data_from_sheet("lessons", username)
    if df_l_raw.empty:
        st.session_state.df_l = pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Ώρα", "Λήξη", "Ποσό", "Κατάσταση", "Πληρώθηκε", "UID"])
    else:
        # ΔΙΟΡΘΩΣΗ: Μετατροπή σε float και επιβολή τύπου για να δέχεται δεκαδικά στο edit
        df_l_raw['Ποσό'] = pd.to_numeric(df_l_raw['Ποσό'].astype(str).str.replace(',', '.'), errors='coerce').fillna(0.0)
        df_l_raw['Ποσό'] = df_l_raw['Ποσό'].astype(float) # <--- ΑΥΤΗ Η ΓΡΑΜΜΗ ΛΥΝΕΙ ΤΟ TYPEERROR
        st.session_state.df_l = df_l_raw

    # 3. Φόρτωση Σημειώσεων
    notes = load_data_from_sheet("notes", username)
    if notes.empty:
        st.session_state.df_n = pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Σημειώσεις", "Αρχείο", "Διαγωνίσματα"])
    else:
        today_str = datetime.now(ZoneInfo('Europe/Athens')).strftime('%Y-%m-%d')
        st.session_state.df_n = notes
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
                    price = round(float(((end - start).total_seconds() / 3600) * float(match['Τιμή'])), 2)
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
                except:
                    st.warning(f"**{r['Μαθητής']}**: {r['Διαγωνίσματα']}")
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
        st.session_state.df_l = st.session_state.df_l.reset_index(drop=True)
# ΤΑΞΙΝΟΜΗΣΗ: ΝΕΑ ΠΡΟΣ ΠΑΛΙΑ
        unpaid = st.session_state.df_l[(st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι")].copy()
        
        if unpaid.empty: 
            st.success("Όλα εξοφλημένα!")
        else:
            unpaid['temp_dt'] = pd.to_datetime(unpaid['Ημερομηνία'] + " " + unpaid['Ώρα'], format="%d/%m/%Y %H:%M", errors='coerce')
            unpaid = unpaid.sort_values('temp_dt', ascending=False).drop(columns=['temp_dt'])
            
            for i, r in unpaid.iterrows():
                # Σωστό Indentation (4 κενά μέσα από το for)
                c1, c2, c3, c4 = st.columns([3, 1.5, 1.5, 2.5])
                
                # 1. Υπολογισμός τρέχουσας διάρκειας από τις ώρες
                try:
                    t1 = datetime.strptime(r['Ώρα'], '%H:%M')
                    t2 = datetime.strptime(r['Λήξη'], '%H:%M')
                    current_hours = (t2 - t1).seconds / 3600
                except:
                    current_hours = 1.0 # Default αν υπάρχει πρόβλημα στο format
                
                c1.write(f"**{r['Μαθητής']}**\n{r['Ημερομηνία']} | {r['Ώρα']}-{r['Λήξη']}")
                
                # 2. Το "Μολυβάκι" αλλάζει τις ΩΡΕΣ
                if st.session_state.get(f"edit_{i}"):
                    new_h = c2.number_input("Ώρες", value=float(current_hours), step=0.25, key=f"h_{i}")
                    if c2.button("💾", key=f"sv_{i}"):
                        # Βρίσκουμε την τιμή ανά ώρα του συγκεκριμένου μαθητή
                        s_price_row = st.session_state.df_s[st.session_state.df_s['Όνομα'] == r['Μαθητής']]
                        s_price = float(s_price_row['Τιμή'].values[0]) if not s_price_row.empty else 0.0
                        
                        # Υπολογισμός νέας λήξης και νέου ποσού
                        new_finish = (t1 + timedelta(hours=new_h)).strftime('%H:%M')
                        st.session_state.df_l.at[i, 'Λήξη'] = new_finish
                        st.session_state.df_l.at[i, 'Ποσό'] = round(float(new_h * s_price), 2)
                        
                        # Κλείδωμα για να μην το αλλάξει το iCloud Sync
                        if not str(st.session_state.df_l.at[i, 'UID']).startswith('locked_'):
                            st.session_state.df_l.at[i, 'UID'] = f"locked_{st.session_state.df_l.at[i, 'UID']}"
                        
                        st.session_state[f"edit_{i}"] = False
                        save_all()
                        st.rerun()
                else:
                    c2.write(f"**{r['Ποσό']:.2f}€**")
                    if c2.button("✏️", key=f"ed_{i}"):
                        st.session_state[f"edit_{i}"] = True
                        st.rerun()
                
                # 3. Είσπραξη και δημιουργία πλεονάσματος
                pay_val = c3.number_input("Είσπραξη", min_value=0.0, value=float(r['Ποσό']), key=f"p_{i}")
                
                if c4.button("✔️", key=f"ok_{i}"):
                    diff = round(float(pay_val) - float(r['Ποσό']), 2)
                    st.session_state.df_l.at[i, 'Πληρώθηκε'] = "Ναι"
                    
                    # Αν έδωσε περισσότερα (π.χ. 50 αντί για 40), φτιάξε αρνητική εγγραφή (-10)
                    if diff != 0:
                        new_uid = f"adj_{datetime.now().timestamp()}"
                        adj_entry = pd.DataFrame([[r['Μαθητής'], r['Ημερομηνία'], "00:00", "00:00", -diff, "Ολοκληρώθηκε", "Όχι", new_uid]], 
                                                 columns=st.session_state.df_l.columns)
                        st.session_state.df_l = pd.concat([st.session_state.df_l, adj_entry], ignore_index=True)
                    
                    save_all()
                    st.rerun()

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
                n, ph, pr = st.text_input("Όνομα"), st.text_input("Τηλέφωνο"), st.number_input("Τιμή/ώρα", 0)
                if st.form_submit_button("Αποθήκευση"):
                    st.session_state.df_s = pd.concat([st.session_state.df_s, pd.DataFrame([[n, ph, pr]], columns=st.session_state.df_s.columns)], ignore_index=True)
                    save_all(); st.rerun()

        st.write("---")
        for i, r in st.session_state.df_s.iterrows():
            c1, c2, c3, c4, c5 = st.columns([2.5, 2, 1.5, 0.5, 0.5])
            if c1.button(f"👤 {r['Όνομα']}", key=f"btn_{i}"):
                st.session_state.selected_student = r['Όνομα']
                st.session_state.view_mode = 'card'
                st.rerun()
            
            if st.session_state.get(f"edit_student_{i}"):
                with st.form(f"edit_s_form_{i}"):
                    new_n = st.text_input("Όνομα", value=r['Όνομα'])
                    new_ph = st.text_input("Τηλέφωνο", value=r['Τηλέφωνο'])
                    new_pr = st.number_input("Τιμή/ώρα", value=int(r['Τιμή']))
                    if st.form_submit_button("💾"):
                        st.session_state.df_s.at[i, 'Όνομα'] = new_n
                        st.session_state.df_s.at[i, 'Τηλέφωνο'] = new_ph
                        st.session_state.df_s.at[i, 'Τιμή'] = new_pr
                        st.session_state[f"edit_student_{i}"] = False
                        save_all(); st.rerun()
            else:
                c2.write(r['Τηλέφωνο'])
                c3.write(f"{r['Τιμή']}€/ώρα")
                if c4.button("✏️", key=f"ed_s_{i}"):
                    st.session_state[f"edit_student_{i}"] = True
                    st.rerun()
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
            if not unpaid_df.empty:
                st.write("Εκκρεμή μαθήματα:")
                for _, ur in unpaid_df.iterrows():
                    st.write(f"• {ur['Ημερομηνία']} ({ur['Ώρα']}-{ur['Λήξη']}): **{ur['Ποσό']:.2f}€**")
            
            if balance > 0 and st.button(f"Εξόφληση Όλων"):
                st.session_state.df_l.loc[(st.session_state.df_l['Μαθητής'] == sel) & (st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε"), 'Πληρώθηκε'] = "Ναι"
                save_all(); st.rerun()
        with t2:
            with st.form("note_page", clear_on_submit=True):
                nt = st.text_area("Σημειώσεις")
                ex_date = st.date_input("Ημερομηνία Διαγωνίσματος", value=None, format="DD/MM/YYYY")
                uploaded_file = st.file_uploader("Σύρετε ή επιλέξτε αρχείο για ανέβασμα", type=["pdf", "png", "jpg", "docx"])
                manual_link = st.text_input("Ή επικολλήστε Link Αρχείου (Drive/Dropbox)")
                
                if st.form_submit_button("Αποθήκευση"):
                    final_link = manual_link
                    if uploaded_file is not None:
                        file_path = os.path.join(UPLOAD_DIR, uploaded_file.name)
                        with open(file_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())
                        final_link = file_path
                    d = datetime.now(ZoneInfo('Europe/Athens')).strftime('%d/%m/%Y')
                    ex_val = ex_date.strftime('%Y-%m-%d') if ex_date else ""
                    new_n = pd.DataFrame([[sel, d, nt, final_link, ex_val]], columns=st.session_state.df_n.columns)
                    st.session_state.df_n = pd.concat([st.session_state.df_n, new_n], ignore_index=True)
                    save_all(); st.rerun()
            
            student_notes = st.session_state.df_n[st.session_state.df_n['Μαθητής'] == sel].iloc[::-1]
            for idx, nr in student_notes.iterrows():
                col_n1, col_n2 = st.columns([0.9, 0.1])
                with col_n1:
                    with st.expander(f"📅 {nr['Ημερομηνία']}"):
                        if nr['Σημειώσεις']: st.write(nr['Σημειώσεις'])
                        if nr['Αρχείο']: 
                            if nr['Αρχείο'].startswith("uploads"):
                                 with open(nr['Αρχείο'], "rb") as f:
                                     st.download_button("📂 Λήψη", f, file_name=os.path.basename(nr['Αρχείο']), key=f"dl_{idx}")
                            else:
                                st.link_button("🔗 Link", nr['Αρχείο'])
                        if nr['Διαγωνίσματα'] and nr['Διαγωνίσματα'] != "":
                            try:
                                d_obj = datetime.strptime(nr['Διαγωνίσματα'], '%Y-%m-%d')
                                st.error(f"🚩 Διαγώνισμα: {d_obj.strftime('%d/%m/%Y')}")
                            except:
                                st.error(f"🚩 {nr['Διαγωνίσματα']}")
                if col_n2.button("🗑️", key=f"del_note_{idx}"):
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
                st.session_state.auth, st.session_state.user, st.session_state.cal_url = True, u, row['cal_url'].values[0]
                load_data(u); st.rerun()
            else: st.error("Λάθος στοιχεία!")
        return

    load_data(st.session_state.user)

    if "menu_option" not in st.session_state: 
        st.session_state.menu_option = "📊 Dashboard"
    
    menu = st.sidebar.radio("Μενού:", ["📊 Dashboard", "📅 Πρόγραμμα", "💰 Οικονομικά", "👥 Μαθητές", "⚙️ Ρυθμίσεις"])
    
    if menu != st.session_state.menu_option:
        st.session_state.menu_option = menu
        auto_collapse_sidebar()
        st.rerun()

    if st.sidebar.button("🚪 Log out"): 
        st.session_state.clear(); 
        st.rerun()

    if menu == "📊 Dashboard": show_dashboard()
    elif menu == "📅 Πρόγραμμα":
        st.header("📅 Πρόγραμμα")
        pend = st.session_state.df_l[st.session_state.df_l['Κατάσταση'] == "Προγραμματισμένο"].copy()
        if pend.empty: st.success("Κανένα προγραμματισμένο μάθημα.")
        else:
            pend['temp_dt'] = pd.to_datetime(pend['Ημερομηνία'] + " " + pend['Ώρα'], format="%d/%m/%Y %H:%M", errors='coerce')
            pend = pend.sort_values('temp_dt').drop(columns=['temp_dt'])
            for i, r in pend.iterrows():
                c1, c2, c3 = st.columns([3, 4, 2])
                c1.write(f"**{r['Μαθητής']}**")
                c2.write(f"{r['Ημερομηνία']} | {r['Ώρα']}")
                s_match = st.session_state.df_s[st.session_state.df_s['Όνομα'] == r['Μαθητής']]
                if not s_match.empty:
                    msg = urllib.parse.quote(f"Υπενθυμίζω το μάθημα μας στις {r['Ώρα']}.")
                    c3.link_button("📱 SMS", f"sms:{s_match.iloc[0]['Τηλέφωνο']}?body={msg}")
    elif menu == "💰 Οικονομικά": show_finance_section()
    elif menu == "👥 Μαθητές": show_student_management()
    elif menu == "⚙️ Ρυθμίσεις": show_settings()

if __name__ == "__main__":
    main()
