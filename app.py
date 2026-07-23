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
import recurring_ical_events

# --- 1. ΒΟΗΘΗΤΙΚΕΣ ΣΥΝΑΡΤΗΣΕΙΣ & ΡΥΘΜΙΣΕΙΣ ---
def auto_apply_credits():
    if 'Πιστωτικό' not in st.session_state.df_s.columns:
        st.session_state.df_s['Πιστωτικό'] = 0.0
    
    # Αν για κάποιο λόγο δεν έχει φορτωθεί η νέα στήλη, τη δημιουργούμε προσωρινά
    if 'Οφειλόμενο Ποσό' not in st.session_state.df_l.columns:
        st.session_state.df_l['Οφειλόμενο Ποσό'] = st.session_state.df_l['Ποσό']

    for idx, student in st.session_state.df_s.iterrows():
        student_name = student['Όνομα']
        
        try:
            credit = float(str(student.get('Πιστωτικό', 0.0)).strip())
        except (ValueError, TypeError):
            credit = 0.0
        
        if credit > 0:
            unpaid_mask = (
                (st.session_state.df_l['Μαθητής'] == student_name) & 
                (st.session_state.df_l['Πληρώθηκε'] == 'Όχι') & 
                (st.session_state.df_l['Κατάσταση'] == 'Ολοκληρώθηκε')
            )
            
            # Ταξινομούμε ώστε να πληρωθούν πρώτα τα παλαιότερα μαθήματα
            unpaid_indices = st.session_state.df_l[unpaid_mask].sort_values(by=['Ημερομηνία', 'Ώρα']).index
            
            for l_idx in unpaid_indices:
                if credit <= 0: 
                    break
                
                try:
                    owed_amt = float(st.session_state.df_l.at[l_idx, 'Οφειλόμενο Ποσό'])
                except (ValueError, TypeError):
                    owed_amt = float(st.session_state.df_l.at[l_idx, 'Ποσό'])
                
                if owed_amt <= 0:
                    continue
                
                if credit >= owed_amt:
                    # Πλήρης εξόφληση του συγκεκριμένου μαθήματος
                    st.session_state.df_l.at[l_idx, 'Οφειλόμενο Ποσό'] = 0.0
                    st.session_state.df_l.at[l_idx, 'Πληρώθηκε'] = 'Ναι'
                    credit -= owed_amt
                else:
                    # Μερική εξόφληση: Μειώνεται η οφειλή του μαθήματος, το μάθημα παραμένει "Όχι"
                    st.session_state.df_l.at[l_idx, 'Οφειλόμενο Ποσό'] = round(owed_amt - credit, 2)
                    credit = 0.0
                    break # Ο κουμπαράς άδειασε, σταματάμε
            
            # Ενημέρωση του εναπομείναντος πιστωτικού στον μαθητή
            st.session_state.df_s.at[idx, 'Πιστωτικό'] = round(credit, 2)

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
        idx = users[username == users['username']].index[0] + 2
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
        # Προσθήκη της στήλης 'Οφειλόμενο Ποσό' στα default columns
        st.session_state.df_l = pd.DataFrame(columns=["Μαθητής", "Ημερομηνία", "Ώρα", "Λήξη", "Ποσό", "Οφειλόμενο Ποσό", "Κατάσταση", "Πληρώθηκε", "UID"])
    else:
        df_l_raw['Ποσό'] = df_l_raw['Ποσό'].apply(clean_currency)
        
        # ΕΔΩ ΜΠΑΙΝΕΙ Η ΔΙΟΡΘΩΣΗ: Αν δεν υπάρχει η στήλη στο Sheets, τη δημιουργεί παίρνοντας τις τιμές του 'Ποσό'
        if 'Οφειλόμενο Ποσό' not in df_l_raw.columns:
            df_l_raw['Οφειλόμενο Ποσό'] = df_l_raw['Ποσό']
        else:
            df_l_raw['Οφειλόμενο Ποσό'] = df_l_raw['Οφειλόμενο Ποσό'].apply(clean_currency)
            
        df_l_raw = df_l_raw.drop_duplicates(subset=['Μαθητής', 'Ημερομηνία', 'Ώρα'], keep='last')
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
    if 'df_l' in st.session_state:
        st.session_state.df_l = st.session_state.df_l.drop_duplicates(subset=['Μαθητής', 'Ημερομηνία', 'Ώρα'], keep='last')
    save_data_to_sheet(st.session_state.df_s, "students", user)
    save_data_to_sheet(st.session_state.df_l, "lessons", user)
    save_data_to_sheet(st.session_state.df_n, "notes", user)
    st.cache_data.clear()
    st.session_state.last_load = datetime.now()

# --- 5. ΣΥΓΧΡΟΝΙΣΜΟΣ ICLOUD (UPDATED) ---

def auto_sync():
    cal_url = st.session_state.cal_url
    if not cal_url or str(cal_url) == "nan": return
    try:
        res = requests.get(cal_url, timeout=5)
        gcal = Calendar.from_ical(res.content)
        gr_tz = ZoneInfo('Europe/Athens')
        now = datetime.now(gr_tz).replace(tzinfo=None)

        # Καθαρισμός: Κρατάμε μόνο χειροκίνητα, κλειδωμένα ή ήδη πληρωμένα μαθήματα
        st.session_state.df_l = st.session_state.df_l[
            (st.session_state.df_l['Κατάσταση'] != "Προγραμματισμένο") | 
            (st.session_state.df_l['UID'].astype(str).str.startswith('locked_')) |
            (st.session_state.df_l['UID'].astype(str).str.startswith('manual_'))
        ].reset_index(drop=True)

        # Ορίζουμε το χρονικό παράθυρο: 7 ημέρες πίσω έως 7 ημέρες μπροστά
        start_limit = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0)
        end_limit = (now + timedelta(days=7)).replace(hour=23, minute=59, second=59)
        
        new_lessons = []
        existing_uids = st.session_state.df_l['UID'].astype(str).tolist()

        # Χρήση της νέας βιβλιοθήκης για αυτόματη εμφάνιση των επαναλαμβανόμενων γεγονότων
        events = recurring_ical_events.of(gcal).between(start_limit, end_limit)

        for comp in events:
            summary = str(comp.get('summary', ''))
            base_uid = str(comp.get('uid', ''))
            
            # Φιλτράρουμε ώστε να διαβάζει μόνο όσα ξεκινούν με "Μάθημα"
            if not summary.strip().lower().startswith("μάθημα"): continue
            
            start = comp.get('dtstart').dt
            
            # Διόρθωση αν το event έχει καταχωρηθεί ως ολοήμερο (date αντί για datetime)
            if type(start) is date:
                start = datetime.combine(start, datetime.min.time())
            start = start.astimezone(gr_tz).replace(tzinfo=None)
            
            end = comp.get('dtend').dt if comp.get('dtend') else start + timedelta(hours=1)
            if type(end) is date:
                end = datetime.combine(end, datetime.min.time())
            end = end.astimezone(gr_tz).replace(tzinfo=None)

            # Δημιουργία ΜΟΝΑΔΙΚΟΥ UID για την συγκεκριμένη ημερομηνία (π.χ. uid_20260704)
            occurrence_uid = f"{base_uid}_{start.strftime('%Y%m%d')}"
            
            # Έλεγχος αν αυτό το συγκεκριμένο μάθημα αυτής της εβδομάδας υπάρχει ήδη
            if occurrence_uid in existing_uids or f"locked_{occurrence_uid}" in existing_uids:
                continue

            # Αναζήτηση μαθητή βάσει ονόματος στο summary
            match = next((s for _, s in st.session_state.df_s.iterrows() if s['Όνομα'].lower() in summary.lower()), None)
            if match is not None:
                d_str = start.strftime('%d/%m/%Y')
                t_start = start.strftime('%H:%M')
                t_end = end.strftime('%H:%M')
                price = round(float(((end - start).total_seconds() / 3600) * float(match['Τιμή'])), 2)
                
                # Αν η ώρα του μαθήματος πέρασε, γίνεται αυτόματα "Ολοκληρώθηκε"
                status = "Ολοκληρώθηκε" if now >= end else "Προγραμματισμένο"
                
                # Προσθέτουμε το 'price' δύο φορές στη σειρά (για Ποσό και Οφειλόμενο Ποσό)
                new_lessons.append([match['Όνομα'], d_str, t_start, t_end, price, price, status, "Όχι", occurrence_uid])
        
        if new_lessons:
            new_df = pd.DataFrame(new_lessons, columns=st.session_state.df_l.columns)
            st.session_state.df_l = pd.concat([st.session_state.df_l, new_df], ignore_index=True)
    
        # Αφαίρεση τυχόν διπλοεγγραφών για σιγουριά
        st.session_state.df_l = st.session_state.df_l.drop_duplicates(subset=['Μαθητής', 'Ημερομηνία', 'Ώρα'], keep='last')
        auto_apply_credits()
        save_all()
    except Exception as e:
        st.error(f"Σφάλμα συγχρονισμού: {e}")

# --- ΛΕΙΤΟΥΡΓΙΑ ΑΥΤΟΜΑΤΗΣ ΜΕΤΑΦΟΡΑΣ ΜΕΤΑ ΤΗ ΛΗΞΗ (UPDATED) ---

def check_and_move_expired_lessons():
    gr_tz = ZoneInfo('Europe/Athens')
    now = datetime.now(gr_tz).replace(tzinfo=None)
    changed = False
    
    if 'df_l' in st.session_state and not st.session_state.df_l.empty:
        for idx, row in st.session_state.df_l.iterrows():
            if row['Κατάσταση'] == "Προγραμματισμένο":
                try:
                    end_dt = datetime.strptime(f"{row['Ημερομηνία']} {row['Λήξη']}", "%d/%m/%Y %H:%M")
                    if now >= end_dt:
                        st.session_state.df_l.at[idx, 'Κατάσταση'] = "Ολοκληρώθηκε"
                        changed = True
                except:
                    continue
    if changed:
        auto_apply_credits() # <--- Προστέθηκε για να ελέγχει απευθείας τα υπόλοιπα
        save_all()

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
                        # Προσθήκη του float(p_m) δύο φορές (για Ποσό και Οφειλόμενο Ποσό)
                        new_l = pd.DataFrame([[sel_m, d_m, ts, te, float(p_m), float(p_m), "Ολοκληρώθηκε", "Όχι", uid_m]], columns=st.session_state.df_l.columns)
                        st.session_state.df_l = pd.concat([st.session_state.df_l, new_l], ignore_index=True)
                        auto_apply_credits() # <--- ΠΡΟΣΘΗΚΗ ΕΔΩ
                        save_all()
                        st.rerun()
        st.divider()
        unpaid = st.session_state.df_l[(st.session_state.df_l['Κατάσταση'] == "Ολοκληρώθηκε") & (st.session_state.df_l['Πληρώθηκε'] == "Όχι")].copy()
        if unpaid.empty: st.success("Όλα εξοφλημένα!")
        else:
            unpaid['temp_dt'] = pd.to_datetime(unpaid['Ημερομηνία'] + " " + unpaid['Ώρα'], format="%d/%m/%Y %H:%M", errors='coerce')
            unpaid = unpaid.sort_values('temp_dt', ascending=False).drop(columns=['temp_dt'])
            for i, r in unpaid.iterrows():
                # Κρατάμε μόνο 2 στήλες: μία για τα στοιχεία και μία για τις ενέργειες
                c1, c2 = st.columns([3, 1])
                
                try:
                    t1 = datetime.strptime(r['Ώρα'], '%H:%M')
                    t2 = datetime.strptime(r['Λήξη'], '%H:%M')
                    current_hours = (t2 - t1).seconds / 3600
                except: current_hours = 1.0
                
                c1.write(f"**{r['Μαθητής']}**\n{r['Ημερομηνία']} | {r['Ώρα']}-{r['Λήξη']} | **{r['Ποσό']:.1f}€**")
                
                if st.session_state.get(f"edit_{i}"):
                    new_h = c2.number_input("Ώρες", value=float(current_hours), step=0.25, key=f"h_{i}", label_visibility="collapsed")
                    if c2.button("💾", key=f"sv_{i}"):
                        s_price_row = st.session_state.df_s[st.session_state.df_s['Όνομα'] == r['Μαθητής']]
                        if not s_price_row.empty:
                            student_idx = s_price_row.index[0]
                            s_price = float(s_price_row['Τιμή'].values[0])
                            
                            # 1. Βρίσκουμε πόσα χρήματα είχαν ήδη αφαιρεθεί/πληρωθεί για αυτό το μάθημα
                            old_total = float(r['Ποσό'])
                            old_owed = float(r['Οφειλόμενο Ποσό']) if 'Οφειλόμενο Ποσό' in r else old_total
                            already_paid = round(old_total - old_owed, 2)
                            
                            # 2. Υπολογίζουμε το νέο συνολικό κόστος
                            new_total = round(new_h * s_price, 2)
                            
                            # 3. Υπολογίζουμε το νέο οφειλόμενο ποσό αφαιρώντας όσα είχαν ήδη πληρωθεί
                            new_owed = round(new_total - already_paid, 2)
                            
                            # Έλεγχος αν οι ώρες μειώθηκαν τόσο που το "έναντι" καλύπτει παραπάνω από το νέο κόστος
                            if new_owed < 0:
                                refund_amount = abs(new_owed)
                                # Επιστρέφουμε τη διαφορά στο γενικό Πιστωτικό του μαθητή
                                old_credit = float(st.session_state.df_s.at[student_idx, 'Πιστωτικό'])
                                st.session_state.df_s.at[student_idx, 'Πιστωτικό'] = round(old_credit + refund_amount, 2)
                                new_owed = 0.0
                                st.session_state.df_l.at[i, 'Πληρώθηκε'] = 'Ναι'
                            
                            # 4. Ενημέρωση των στοιχείων του μαθήματος
                            st.session_state.df_l.at[i, 'Λήξη'] = (t1 + timedelta(hours=new_h)).strftime('%H:%M')
                            st.session_state.df_l.at[i, 'Ποσό'] = new_total
                            st.session_state.df_l.at[i, 'Οφειλόμενο Ποσό'] = new_owed
                            
                            if not str(st.session_state.df_l.at[i, 'UID']).startswith('locked_'):
                                st.session_state.df_l.at[i, 'UID'] = f"locked_{st.session_state.df_l.at[i, 'UID']}"
                            
                            st.session_state[f"edit_{i}"] = False
                            auto_apply_credits()  # Τρέχει ξανά έλεγχο σε περίπτωση που δημιουργήθηκε νέο πιστωτικό
                            save_all()
                            st.rerun()
                else:
                    # Εδώ έχουμε το μολυβάκι για επεξεργασία και το ✖️ για διαγραφή
                    col_edit, col_del = c2.columns(2)
                    if col_edit.button("✏️", key=f"ed_{i}"): st.session_state[f"edit_{i}"] = True; st.rerun()
                    if col_del.button("✖️", key=f"no_{i}", help="Δεν πραγματοποιήθηκε"):
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
                selected_students = st.multiselect("Επιλογή Μαθητών (για Σύνολο ή Οικογένεια)", all_students_in_month)
                
                if selected_students:
                    df_family = df_f[df_f['Μαθητής'].isin(selected_students)]
                    total_family_revenue = df_family['Ποσό'].sum()
                    unpaid_family = df_family[df_family['Πληρώθηκε'] == 'Όχι']['Ποσό'].sum()
                    
                    with st.container(border=True):
                        st.subheader("👨‍👩‍👧‍👦 Σύνολο Επιλεγμένων (Οικογένεια)")
                        col_f1, col_f2 = st.columns(2)
                        col_f1.metric("Συνολικά Έσοδα", f"{total_family_revenue:.2f} €")
                        col_f2.metric("Ανεξόφλητο Υπόλοιπο", f"{unpaid_family:.2f} €")
                        
                        phones = {}
                        details = []
                        for s in selected_students:
                            c = len(df_family[df_family['Μαθητής'] == s])
                            if c > 0:
                                details.append(f"{c} {'μάθημα' if c==1 else 'μαθήματα'} στον/στην {s}")
                            
                            s_info = st.session_state.df_s[st.session_state.df_s['Όνομα'] == s]
                            if not s_info.empty:
                                ph = str(s_info.iloc[0]['Τηλέφωνο'])
                                phones[f"{s} ({ph})"] = ph
                        
                        target_phone_label = st.radio("Αποστολή SMS στο τηλέφωνο του/της:", list(phones.keys()), horizontal=True)
                        target_phone = phones[target_phone_label]
                        
                        summary_text = " και ".join(details)
                        now_hour = datetime.now(gr_tz).hour
                        greeting = "Καλημέρα σας," if now_hour < 13 else "Καλησπέρα σας,"
                        family_msg = f"{greeting} αυτόν τον μήνα έχουν γίνει {summary_text} και το συνολικό υπόλοιπο είναι {unpaid_family:.2f}€."
                        
                        txt_encoded = urllib.parse.quote(family_msg)
                        st.link_button(f"📱 Αποστολή SMS στην Οικογένεια", f"sms:{target_phone}?body={txt_encoded}", use_container_width=True)
                
                st.divider()
                
                st.subheader("👤 Αναφορά ανά Μαθητή")
                summary = df_f.groupby('Μαθητής').agg({'Ποσό': 'sum', 'Ημερομηνία': 'count'}).reset_index()
                for _, row in summary.iterrows():
                    with st.expander(f"{row['Μαθητής']} | Σύνολο: {row['Ποσό']:.2f} €"):
                        s_info = st.session_state.df_s[st.session_state.df_s['Όνομα'] == row['Μαθητής']]
                        if not s_info.empty:
                            now_hour = datetime.now(ZoneInfo('Europe/Athens')).hour
                            greeting = "Καλημέρα σας," if now_hour < 13 else "Καλησπέρα σας,"
                            count = int(row['Ημερομηνία'])
                            lesson_text = "έχει γίνει 1 μάθημα" if count == 1 else f"έχουν γίνει {count} μαθήματα"
                            unpaid_amount = df_f[(df_f['Μαθητής'] == row['Μαθητής']) & (df_f['Πληρώθηκε'] == 'Όχι')]['Ποσό'].sum()
                            full_msg = f"{greeting} αυτόν τον μήνα {lesson_text} και το υπόλοιπο είναι {unpaid_amount:.2f}€."
                            txt = urllib.parse.quote(full_msg)
                            st.link_button(f"📱 Αποστολή SMS", f"sms:{s_info.iloc[0]['Τηλέφωνο']}?body={txt}")
                        
                        for _, det in df_f[df_f['Μαθητής'] == row['Μαθητής']].iterrows():
                            st.write(f"{'✅' if det['Πληρώθηκε']=='Ναι' else '⏳'} {det['Ημερομηνία']}: {det['Ποσό']:.2f}€")
            else:
                st.info("Δεν βρέθηκαν ολοκληρωμένα μαθήματα για αυτόν τον μήνα.")

def show_student_management():
    if 'view_mode' not in st.session_state: st.session_state.view_mode = 'list'
    if st.session_state.view_mode == 'list':
        st.header("👥 Διαχείριση Μαθητών")
        with st.expander("➕ Προσθήκη Μαθητή"):
            with st.form("add_s_new"):
                n, ph, pr = st.text_input("Όνομα"), st.text_input("Τηλέφωνο"), st.number_input("Τιμή/ώρα", 0.0, format="%.2f")
                if st.form_submit_button("Αποθήκευση"):
                    new_student_df = pd.DataFrame([[n, str(ph), pr, 0.0]], columns=st.session_state.df_s.columns)
                    st.session_state.df_s = pd.concat([st.session_state.df_s, new_student_df], ignore_index=True)
                    save_all()
                    st.rerun()

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
            if 'df_l' in st.session_state:
                student_match = st.session_state.df_s[st.session_state.df_s['Όνομα'] == sel]
                if not student_match.empty:
                    student_idx = student_match.index[0]
                    
                    if 'Οφειλόμενο Ποσό' not in st.session_state.df_l.columns:
                        st.session_state.df_l['Οφειλόμενο Ποσό'] = st.session_state.df_l['Ποσό']
                    
                    unpaid_mask = (
                        (st.session_state.df_l['Μαθητής'] == sel) & 
                        (st.session_state.df_l['Πληρώθηκε'] == 'Όχι') & 
                        (st.session_state.df_l['Κατάσταση'] == 'Ολοκληρώθηκε')
                    )

                    # Το άθροισμα πλέον υπολογίζεται από τη στήλη των πραγματικών οφειλών
                    unpaid_sum = pd.to_numeric(st.session_state.df_l[unpaid_mask]['Οφειλόμενο Ποσό'], errors='coerce').sum()
                    
                    try:
                        current_credit = float(st.session_state.df_s.at[student_idx, 'Πιστωτικό'])
                    except:
                        current_credit = 0.0
                    
                    actual_balance = round(float(unpaid_sum) - current_credit, 2)
                    
                    if actual_balance < 0:
                        st.metric("Συνολικό Υπόλοιπο", f"{actual_balance:.2f} €", "Προπληρωμή", delta_color="normal")
                    elif actual_balance > 0:
                        st.metric("Συνολικό Υπόλοιπο", f"{actual_balance:.2f} €", "Οφειλή", delta_color="inverse")
                    else:
                        st.metric("Συνολικό Υπόλοιπο", "0.00 €", "Εξοφλημένος", delta_color="off")
                    
                    # Λίστα Εκκρεμών με τις δύο στήλες
                    st.write("### Αναλυτικές Εκκρεμότητες Μαθημάτων")
                    student_unpaid = st.session_state.df_l[unpaid_mask]
                    if student_unpaid.empty:
                        st.info("Δεν υπάρχουν εκκρεμή οφειλόμενα μαθήματα.")
                    else:
                        for l_idx, hr in student_unpaid.iterrows():
                            with st.container(border=True):
                                col_a, col_b = st.columns([6, 4])
                                col_a.write(f"📅 {hr['Ημερομηνία']} | {hr['Ώρα']} - {hr['Λήξη']}")
                                col_b.write(f"📊 Συνολικό Κόστος: {float(hr['Ποσό']):.2f}€ | ⏳ Οφειλόμενο Ποσό: {float(hr['Οφειλόμενο Ποσό']):.2f}€")

                    st.write("---")
                    col1, col2, col3 = st.columns([1, 1.2, 1.2])
                    with col1:
                        if st.button("Εξόφληση όλων", key=f"pay_all_{sel}"):
                            st.session_state.df_l.loc[unpaid_mask, 'Οφειλόμενο Ποσό'] = 0.0
                            st.session_state.df_l.loc[unpaid_mask, 'Πληρώθηκε'] = 'Ναι'
                            st.session_state.df_s.at[student_idx, 'Πιστωτικό'] = 0.0
                            save_all(); st.rerun()
                    with col2:
                        custom_amount = st.number_input("Ποσό Πληρωμής (€)", min_value=0.0, step=5.0, key=f"amt_in_{sel}")
                    with col3:
                        if st.button("Εξόφληση Χ ποσού", key=f"pay_x_{sel}"):
                            if custom_amount > 0:
                                old_credit = float(st.session_state.df_s.at[student_idx, 'Πιστωτικό'])
                                st.session_state.df_s.at[student_idx, 'Πιστωτικό'] = round(old_credit + custom_amount, 2)
                                auto_apply_credits()
                                save_all(); st.success("Η πληρωμή καταχωρήθηκε!"); st.rerun()
                            else: 
                                st.error("Εισάγετε ποσό > 0")
                else:
                    st.error("Ο μαθητής δεν βρέθηκε στη βάση δεδομένων.")
            else:
                st.warning("Γίνεται φόρτωση των δεδομένων...")
        
        # ... το υπόλοιπο της συνάρτησης (t2, t3) παραμένει ως έχει ...
                
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
                if 'Οφειλόμενο Ποσό' not in hist.columns:
                    hist['Οφειλόμενο Ποσό'] = hist['Ποσό']
                hist['temp_dt'] = pd.to_datetime(hist['Ημερομηνία'], format="%d/%m/%Y", errors='coerce')
                hist = hist.sort_values('temp_dt', ascending=False).drop(columns=['temp_dt'])
                for idx, hr in hist.iterrows():
                    hc1, hc2 = st.columns([9, 1])
                    icon = "✅" if hr['Πληρώθηκε'] == "Ναι" else "⏳"
                    hc1.write(f"{icon} {hr['Ημερομηνία']} | {hr['Ώρα']} - {hr['Λήξη']} | 📊 Συνολικό Κόστος: {hr['Ποσό']:.2f} € | ⏳ Οφειλόμενο Ποσό: {float(hr['Οφειλόμενο Ποσό']):.2f} €")
                    if hc2.button("🗑️", key=f"del_hist_{idx}"):
                        st.session_state.df_l = st.session_state.df_l.drop(idx)
                        save_all(); st.rerun()

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
    check_and_move_expired_lessons()
    
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
            gr_tz = ZoneInfo('Europe/Athens')
            now_dt = datetime.now(gr_tz)
            today_str = now_dt.strftime('%d/%m/%Y')
            tomorrow_str = (now_dt + timedelta(days=1)).strftime('%d/%m/%Y')
            current_hour = now_dt.hour
            greeting = "Καλή συνέχεια!" if current_hour >= 13 else "Καλή σας ημέρα!"

            pend['temp_sort_dt'] = pd.to_datetime(pend['Ημερομηνία'] + " " + pend['Ώρα'], format="%d/%m/%Y %H:%M", errors='coerce')
            pend = pend.sort_values('temp_sort_dt', ascending=True).drop(columns=['temp_sort_dt'])
            
            for i, r in pend.iterrows():
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 4, 2])
                    c1.write(f"**{r['Μαθητής']}**")
                    c2.write(f"{r['Ημερομηνία']} | {r['Ώρα']} - {r['Λήξη']}")
                    
                    s_match = st.session_state.df_s[st.session_state.df_s['Όνομα'] == r['Μαθητής']]
                    if not s_match.empty:
                        if r['Ημερομηνία'] == today_str:
                            day_label = "το σημερινό μας μάθημα"
                        elif r['Ημερομηνία'] == tomorrow_str:
                            day_label = "το αυριανό μας μάθημα"
                        else:
                            day_label = f"το μάθημά μας στις {r['Ημερομηνία']}"
                        
                        sms_text = f"Υπενθυμίζω {day_label} στις {r['Ώρα']}. {greeting}"
                        encoded_sms = urllib.parse.quote(sms_text)
                        c3.link_button("📱 SMS", f"sms:{s_match.iloc[0]['Τηλέφωνο']}?body={encoded_sms}")

    elif menu == "💰 Οικονομικά": show_finance_section()
    elif menu == "👥 Μαθητές": show_student_management()
    elif menu == "⚙️ Ρυθμίσεις": show_settings()

if __name__ == "__main__": main()
