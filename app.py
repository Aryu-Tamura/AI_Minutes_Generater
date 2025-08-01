import streamlit as st
from openai import OpenAI
import whisper
import torch
from pyannote.audio import Pipeline
from pydub import AudioSegment
import tempfile
import os
from datetime import timedelta, date, datetime
from docx import Document
from docx.shared import Inches
from io import BytesIO
import json
import plotly.graph_objects as go
import logging
import sqlite3
import zipfile

# -------------------------------------------------------------------
# 1. 初期設定 & ロギング・DB設定
# -------------------------------------------------------------------

st.set_page_config(layout="wide", page_title="AI議事録アシスタント")

# ロギングの設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', filename='app.log', filemode='a')

# StreamlitのsecretsからAPIキーを安全に読み込む
try:
    HF_TOKEN = st.secrets["HF_TOKEN"]
    OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
    logging.info("API keys loaded successfully.")
except FileNotFoundError:
    st.error("`.streamlit/secrets.toml`ファイルが見つかりません。APIキーを設定してください。")
    logging.error("secrets.toml not found.")
    st.stop()
except KeyError as e:
    st.error(f"`secrets.toml`に`{e}`が設定されていません。")
    logging.error(f"API key missing in secrets.toml: {e}")
    st.stop()

# 各種クライアントの初期化
WHISPER_MODEL = "small"
client = OpenAI(api_key=OPENAI_API_KEY)

# データベースの初期化
DB_FILE = "database.db"
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            sales_rep TEXT NOT NULL,
            client_company TEXT NOT NULL,
            client_rep TEXT NOT NULL,
            report_date TEXT NOT NULL,
            analysis_json TEXT NOT NULL,
            report_markdown TEXT
        )
    ''')
    conn.commit()
    conn.close()
    logging.info("Database initialized.")
init_db()

# -------------------------------------------------------------------
# 2. セッションステートの管理
# -------------------------------------------------------------------
def reset_creation_page_state():
    """商談レポート作成ページの状態をリセットする関数"""
    st.session_state.analysis_stage = "initial"
    st.session_state.negotiation_info = {}
    st.session_state.analysis_data = None
    st.session_state.transcript_display = []
    st.session_state.chat_history = []
    st.session_state.report_for_display = ""
    st.session_state.uploaded_file = None
    st.session_state.show_modal = False
    st.session_state.current_report_id = None
    st.session_state.report_saved = False
    logging.info("Creation page state has been reset.")

if "current_page" not in st.session_state:
    st.session_state.current_page = "creation"
    reset_creation_page_state()

# -------------------------------------------------------------------
# 3. OpenAI GPT API 関連関数
# -------------------------------------------------------------------
def get_initial_analysis(transcript_text, negotiation_info):
    """文字起こしデータから、初回分析結果を生成する"""
    system_prompt = "あなたは、非常に優秀なAIアシスタントです。提供された商談の文字起こしデータと事前情報を分析し、プロフェッショナルな視点から、指定されたJSONフォーマットで回答を生成してください。"
    user_prompt = f"""
以下の商談の文字起こしと事前情報を分析し、JSON形式で出力してください。
### 出力例 (Few-shot)
```json
{{
  "cleaned_transcript": [
    {{ "speaker": "田中", "text": "本日はありがとうございます。田中です。", "start_time": "00:00:01" }}
  ],
  "summary_report": {{
    "overview": {{"date": "2025年08月01日", "attendees": {{"client_company": "株式会社サンプル", "client_rep": "鈴木 様", "our_company": "田中"}}}},
    "agenda": "新サービス導入に関する最終確認", "summary": "新サービスのプランAを軸に検討を進めることで合意。",
    "decisions": ["プランAを軸に検討を進める。"], "todos": ["(田中) セキュリティに関する詳細説明を来週水曜日に行う。"],
    "concerns": ["顧客はセキュリティ面を懸念している。"], "notes": "特になし"
  }},
  "deep_analysis": {{
    "balance_ratio": 60, "balance_feedback": "営業担当者の発話がやや多めです。",
    "success_score": 75, "score_reason": "顧客が具体的な懸念点を提示しているため。",
    "question_feedback": "クローズドな質問が中心でした。", "next_step_score": "A",
    "next_step_feedback": "具体的な次のアクションが設定されており、明確です。",
    "better_negotiation_tips": {{
        "advice": "顧客の懸念に対して、より共感を示し、具体的な解決策を提示することで、信頼関係が深まります。",
        "example_questions": ["「セキュリティについて、特にご懸念されているのはどのような点でしょうか？」", "「もし、その懸念が解消されるとしたら、導入に向けて前向きにご検討いただけますでしょうか？」"]
    }}
  }}
}}
```
---
### あなたへの指示
上記の出力例を参考に、以下の商談の文字起こしと事前情報を分析し、同じJSON形式で出力してください。
### 事前情報
- 商談日時: {negotiation_info['date']}
- 営業担当者: {negotiation_info['sales_rep']}
- 顧客企業名: {negotiation_info['client_company']}
- 顧客担当者名: {negotiation_info['client_rep']}
### 文字起こしデータ (話者名 (HH:MM:SS): 発言内容)
{transcript_text}
"""
    try:
        logging.info("Requesting initial analysis from GPT-4o.")
        response = client.chat.completions.create(model="gpt-4o", response_format={"type": "json_object"}, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], temperature=0.3)
        logging.info("Successfully received initial analysis from GPT-4o.")
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        st.error(f"OpenAI APIでの分析中にエラー: {e}"); logging.error(f"Error during initial analysis: {e}"); return None

def get_refined_report(original_report, user_instruction):
    """ユーザーの指示に基づき、レポートを修正する"""
    system_prompt = "あなたは優秀なアシスタントです。ユーザーの指示に従って、提供されたレポートを修正してください。必ずレポート全体の構造を維持したまま、指示された箇所のみを修正し、修正後のレポート全文を出力してください。"
    user_prompt = f"""
### 指示の例 (Few-shot)

#### 元のレポート:
### 1. 商談概要
* **日時**: 2025年08月01日
* **出席者**: 田中、鈴木
### 2. 決定事項
* Aプランを軸に検討を進めることになった。
* 来週、セキュリティに関する詳細な説明を行うための会議を設定する。

#### 修正指示:
決定事項を箇条書きの一つにまとめてください。

#### 期待される出力:
### 1. 商談概要
* **日時**: 2025年08月01日
* **出席者**: 田中、鈴木
### 2. 決定事項
* Aプランを軸に検討し、来週セキュリティに関する詳細説明の会議を設定する。

---

### あなたへの指示
上記の例を参考に、以下のレポートを次の指示に従って修正してください。

### 元のレポート:
{original_report}

### 修正指示:
{user_instruction}
"""
    try:
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}])
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"レポートの修正中にエラー: {e}"); return original_report

# -------------------------------------------------------------------
# 4. ヘルパー関数 (Wordファイル生成, DB操作など)
# -------------------------------------------------------------------
def format_timestamp(seconds):
    """秒をHH:MM:SS形式の文字列に変換する"""
    return str(timedelta(seconds=int(seconds)))

def create_minutes_docx(report_text):
    doc = Document()
    lines = report_text.split('\n')
    for line in lines:
        line = line.strip()
        if line.startswith('### '): doc.add_heading(line.replace('### ', ''), level=2)
        elif line.startswith('**'): p = doc.add_paragraph(); p.add_run(line.replace('**', '').replace('*', '')).bold = True
        elif line.startswith('* '): doc.add_paragraph(line.replace('* ', ''), style='List Bullet')
        elif line.strip(): doc.add_paragraph(line)
    bio = BytesIO(); doc.save(bio); bio.seek(0); return bio.getvalue()

def create_analysis_docx(analysis_data, negotiation_info, fig):
    doc = Document()
    analysis = analysis_data.get('deep_analysis', {})
    tips = analysis.get('better_negotiation_tips', {})
    doc.add_heading('AIによる商談分析レポート', 0)
    doc.add_paragraph(f"企業名: {negotiation_info.get('client_company', 'N/A')}")
    doc.add_paragraph(f"営業担当: {negotiation_info.get('sales_rep', 'N/A')}")
    doc.add_paragraph(f"日時: {negotiation_info.get('date', 'N/A')}")
    doc.add_heading('総合評価', level=1)
    doc.add_paragraph(f"商談成功確度: {analysis.get('success_score', 'N/A')}点")
    doc.add_paragraph(f"根拠: {analysis.get('score_reason', 'N/A')}")
    doc.add_heading('会話バランス', level=2)
    chart_path = "temp_chart.png"; fig.write_image(chart_path, scale=2); doc.add_picture(chart_path, width=Inches(5.0)); os.remove(chart_path)
    doc.add_paragraph(f"フィードバック: {analysis.get('balance_feedback', 'N/A')}")
    doc.add_heading('より良い商談のために', level=1)
    doc.add_paragraph(f"アドバイス: {tips.get('advice', 'N/A')}")
    doc.add_heading('具体的な質問例', level=2)
    for q in tips.get('example_questions', []): doc.add_paragraph(q, style='List Bullet')
    bio = BytesIO(); doc.save(bio); bio.seek(0); return bio.getvalue()

def save_report_to_db(negotiation_info, analysis_data, report_markdown):
    """分析結果と最終レポートをSQLiteデータベースに保存する"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO reports (timestamp, sales_rep, client_company, client_rep, report_date, analysis_json, report_markdown)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now().isoformat(), negotiation_info['sales_rep'], negotiation_info['client_company'],
        negotiation_info['client_rep'], negotiation_info['date'],
        json.dumps(analysis_data, ensure_ascii=False), report_markdown
    ))
    conn.commit()
    conn.close()
    logging.info(f"Report for {negotiation_info['client_company']} saved to database.")

# -------------------------------------------------------------------
# 5. UI描画: サイドバー
# -------------------------------------------------------------------

with st.sidebar:
    st.header("AI議事録アシスタント")
    st.markdown("---")
    menu_items = {"creation": "商談レポート作成", "history": "過去のレポート", "feedback": "フィードバック"}
    
    for key, value in menu_items.items():
        if st.button(value, use_container_width=True, type="primary" if st.session_state.current_page == key else "secondary"):
            st.session_state.current_page = key
            if 'viewing_report_id' in st.session_state:
                del st.session_state['viewing_report_id']
            st.rerun()

# -------------------------------------------------------------------
# 6. UI描画: メインコンテンツ (ページ切り替え)
# -------------------------------------------------------------------

if st.session_state.current_page == "creation":
    st.title("商談レポート作成")

    if st.session_state.analysis_stage != "initial":
        if st.button("新しいレポートを作成する"):
            st.session_state.confirm_reset = True
    
    if 'confirm_reset' not in st.session_state:
        st.session_state.confirm_reset = False
    
    if st.session_state.confirm_reset:
        placeholder = st.empty()
        with placeholder.container(border=True):
            st.warning("現在の作業内容は失われます。新しいレポートを作成しますか？")
            col1, col2 = st.columns(2)
            if col1.button("はい、作成する", use_container_width=True, type="primary"):
                reset_creation_page_state()
                st.session_state.confirm_reset = False
                placeholder.empty()
                st.rerun()
            if col2.button("いいえ", use_container_width=True):
                st.session_state.confirm_reset = False
                placeholder.empty()
                st.rerun()

    if st.session_state.analysis_stage == "initial":
        if st.button("音声ファイルをアップロードして開始", type="primary"):
            st.session_state.show_modal = True
        if 'show_modal' not in st.session_state: st.session_state.show_modal = False
        if st.session_state.show_modal:
            with st.form("upload_form"):
                st.subheader("商談情報の入力とアップロード")
                neg_date = st.date_input("商談日", value=date.today())
                rep_names = ["田中真奈美", "渡辺徹", "小林恭子", "斎藤学", "工藤新一"]
                sales_rep = st.selectbox("営業担当者名", options=rep_names)
                client_company = st.text_input("顧客企業名", placeholder="株式会社デモ")
                client_rep = st.text_input("顧客担当者名", placeholder="商談 花子")
                uploaded_file = st.file_uploader("音声ファイルを選択", type=['wav', 'mp3', 'm4a'])
                submitted = st.form_submit_button("分析を開始する")
                if submitted:
                    if not all([sales_rep, client_company, client_rep, uploaded_file]):
                        st.warning("すべての項目を入力し、ファイルをアップロードしてください。")
                    else:
                        st.session_state.negotiation_info = {"date": neg_date.strftime('%Y年%m月%d日'), "sales_rep": sales_rep, "client_company": client_company, "client_rep": client_rep}
                        st.session_state.uploaded_file = uploaded_file
                        st.session_state.analysis_stage = 'processing'
                        st.session_state.show_modal = False
                        st.rerun()

    if st.session_state.analysis_stage == 'processing':
        uploaded_file = st.session_state.get('uploaded_file')
        if uploaded_file:
            with st.spinner("AIアシスタントが分析中です...完了まで数分かかることがあります。"):
                audio_bytes = uploaded_file.getvalue()
                temp_path, wav_path = None, None
                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                        tmp.write(audio_bytes); temp_path = tmp.name
                    audio = AudioSegment.from_file(temp_path).set_frame_rate(16000).set_sample_width(2).set_channels(1)
                    wav_path = temp_path + ".wav"; audio.export(wav_path, format="wav")
                    
                    diarization_pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=HF_TOKEN)
                    if torch.cuda.is_available(): diarization_pipeline.to(torch.device("cuda"))
                    diarization = diarization_pipeline(wav_path)
                    
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                    whisper_model = whisper.load_model(WHISPER_MODEL, device=device)
                    transcription_result = whisper_model.transcribe(wav_path, word_timestamps=True, language="ja")
                    
                    word_timestamps = [word for segment in transcription_result['segments'] for word in segment['words']]
                    raw_transcript_text = ""
                    if word_timestamps:
                        speaker_turns = [{'start': turn.start, 'end': turn.end, 'speaker': speaker} for turn, _, speaker in diarization.itertracks(yield_label=True)]
                        for word in word_timestamps:
                            word_center = word['start'] + (word['end'] - word['start']) / 2
                            word['speaker'] = next((turn['speaker'] for turn in speaker_turns if turn['start'] <= word_center <= turn['end']), 'UNKNOWN')
                        
                        current_speaker, current_transcript, start_time = word_timestamps[0]['speaker'], "", word_timestamps[0]['start']
                        for word in word_timestamps:
                            if word['speaker'] != current_speaker:
                                raw_transcript_text += f"{current_speaker} ({format_timestamp(start_time)}): {current_transcript.strip()}\n"
                                current_speaker, current_transcript, start_time = word['speaker'], "", word['start']
                            current_transcript += word['word']
                        raw_transcript_text += f"{current_speaker} ({format_timestamp(start_time)}): {current_transcript.strip()}\n"
                except Exception as e:
                    st.error(f"音声処理中にエラーが発生しました: {e}")
                    logging.error(f"Error in audio processing: {e}")
                    st.session_state.analysis_stage = "initial"
                    st.rerun()

                finally:
                    if temp_path and os.path.exists(temp_path): os.remove(temp_path)
                    if wav_path and os.path.exists(wav_path): os.remove(wav_path)

                analysis_result = get_initial_analysis(raw_transcript_text, st.session_state.negotiation_info)
                if analysis_result:
                    st.session_state.analysis_data = analysis_result
                    st.session_state.transcript_display = analysis_result.get('cleaned_transcript', [])
                    st.session_state.analysis_stage = 'done'
                    st.session_state.chat_history = [{"role": "assistant", "content": "レポートを生成しました。"}]
                    st.rerun()
                else:
                    st.error("分析に失敗しました。"); st.session_state.analysis_stage = 'initial'

    if st.session_state.analysis_stage == 'done':
        col1, col2 = st.columns(2)
        analysis_data = st.session_state.analysis_data

        with col1:
            st.subheader("対話型レポート編集")
            chat_container = st.container(height=250)
            with chat_container:
                for message in st.session_state.chat_history:
                    with st.chat_message(message["role"]):
                        st.markdown(message["content"])
            
            if prompt := st.chat_input("レポートの修正指示を入力"):
                st.session_state.chat_history.append({"role": "user", "content": prompt})
                with st.spinner("AIがレポートを修正中です..."):
                    refined_report = get_refined_report(st.session_state.report_for_display, prompt)
                    st.session_state.report_for_display = refined_report
                st.session_state.chat_history.append({"role": "assistant", "content": "レポートを修正しました。"})
                st.rerun()

            st.subheader("文字起こし")
            transcript_container = st.container(height=300)
            with transcript_container:
                for item in st.session_state.transcript_display:
                    st.markdown(f"**{item.get('speaker', '不明')}** ({item.get('start_time', '00:00:00')}): {item.get('text', '')}")

        with col2:
            st.subheader("生成レポート")
            
            if not st.session_state.report_for_display:
                report_data = analysis_data.get('summary_report', {})
                overview = report_data.get('overview', {})
                attendees = overview.get('attendees', {})
                
                report_parts = [
                    f"### 1. 商談概要", f"* **日時**: {overview.get('date', 'N/A')}", f"* **出席者**:",
                    f"  * **{attendees.get('client_company', '顧客企業')}**: {attendees.get('client_rep', 'N/A')}",
                    f"  * **弊社**: {attendees.get('our_company', 'N/A')}",
                    f"### 2. 本日の目的（アジェンダ）", f"* {report_data.get('agenda', 'N/A')}",
                    f"### 3. 主要な議論の要約", f"* {report_data.get('summary', 'N/A')}",
                    f"### 4. 決定事項", "\n".join(f"* {item}" for item in report_data.get('decisions', ['特になし'])),
                    f"### 5. ToDo（ネクストアクション）", "\n".join(f"* {item}" for item in report_data.get('todos', ['特になし'])),
                    f"### 6. 確認事項・懸念点", "\n".join(f"* {item}" for item in report_data.get('concerns', ['特になし'])),
                    f"### 7. その他（特記事項）", f"* {report_data.get('notes', '特になし')}",
                ]
                st.session_state.report_for_display = "\n\n".join(report_parts)
            
            preview_tab, edit_tab = st.tabs(["プレビュー", "編集"])
            with preview_tab:
                st.markdown(st.session_state.report_for_display, unsafe_allow_html=True)
            with edit_tab:
                edited_report = st.text_area("レポート内容を直接編集", st.session_state.report_for_display, height=250, label_visibility="collapsed")
                if edited_report != st.session_state.report_for_display:
                    st.session_state.report_for_display = edited_report
                    st.rerun()
            
            st.subheader("保存とダウンロード")

            def save_current_report():
                if not st.session_state.get('report_saved', False):
                    save_report_to_db(st.session_state.negotiation_info, st.session_state.analysis_data, st.session_state.report_for_display)
                    st.session_state.report_saved = True
                    st.toast("レポートを履歴に保存しました！")

            dl_col1, dl_col2 = st.columns(2)
            with dl_col1:
                minutes_docx = create_minutes_docx(st.session_state.report_for_display)
                st.download_button("議事録ダウンロード", minutes_docx, "議事録.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True, on_click=save_current_report)
            
            with dl_col2:
                analysis = analysis_data.get('deep_analysis', {})
                balance_ratio = analysis.get('balance_ratio', 50)
                colors = ['#636EFA', '#EF553B']
                fig = go.Figure(data=[go.Pie(labels=['顧客', '営業担当'], values=[100 - balance_ratio, balance_ratio], hole=.3, marker_colors=colors)])
                fig.update_traces(hovertemplate='<b>%{label}</b>: %{percent}<extra></extra>')
                fig.update_layout(title_text='会話バランス', height=250, margin=dict(t=50, b=0, l=0, r=0))
                
                analysis_docx = create_analysis_docx(analysis_data, st.session_state.negotiation_info, fig)
                st.download_button("AI分析レポートダウンロード", analysis_docx, "AI分析レポート.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True, on_click=save_current_report)

            st.subheader("AIコーチング")
            st.plotly_chart(fig, use_container_width=True)
            st.info(f"**フィードバック:** {analysis.get('balance_feedback', 'N/A')}")
            st.metric("商談成功確度", f"{analysis.get('success_score', 'N/A')} 点", delta=analysis.get('score_reason', 'N/A'))

elif st.session_state.current_page == "history":
    st.title("過去のレポート一覧")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, report_date, sales_rep, client_company FROM reports ORDER BY timestamp DESC")
    all_reports = c.fetchall()
    conn.close()

    if not all_reports: st.info("保存されているレポートはありません。")
    
    for report in all_reports:
        report_id, report_date, sales_rep, client_company = report
        with st.container(border=True):
            st.subheader(client_company)
            st.write(f"担当: {sales_rep} | 日付: {report_date}")
            if st.button("このレポートを開く", key=f"open_{report_id}"):
                st.session_state.current_page = "viewer"
                st.session_state.viewing_report_id = report_id
                st.rerun()

elif st.session_state.current_page == "viewer":
    st.title("レポート閲覧")
    report_id = st.session_state.get("viewing_report_id")
    if report_id:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT report_markdown FROM reports WHERE id = ?", (report_id,))
        data = c.fetchone()
        conn.close()
        if data:
            st.markdown(data[0], unsafe_allow_html=True)
            if st.button("このレポートを修正する", type="primary"):
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("SELECT analysis_json, report_markdown FROM reports WHERE id = ?", (report_id,))
                full_data = c.fetchone()
                conn.close()
                if full_data:
                    analysis_data = json.loads(full_data[0])
                    st.session_state.analysis_data = analysis_data
                    st.session_state.report_for_display = full_data[1]
                    overview = analysis_data.get('summary_report', {}).get('overview', {})
                    attendees = overview.get('attendees', {})
                    st.session_state.negotiation_info = {
                        "date": overview.get('date', 'N/A'),
                        "sales_rep": attendees.get('our_company', 'N/A'),
                        "client_company": attendees.get('client_company', 'N/A'),
                        "client_rep": attendees.get('client_rep', 'N/A')
                    }
                    st.session_state.transcript_display = analysis_data.get('cleaned_transcript', [])
                    st.session_state.analysis_stage = "done"
                    st.session_state.current_page = "creation"
                    st.session_state.report_saved = True
                    st.rerun()

elif st.session_state.current_page == "feedback":
    st.title("営業担当者フィードバック")
    rep_names = ["田中真奈美", "渡辺徹", "小林恭子", "斎藤学", "工藤新一"]
    selected_name = st.selectbox("フィードバックを見る担当者を選択してください", options=rep_names)
    
    if selected_name:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT analysis_json FROM reports WHERE sales_rep = ?", (selected_name,))
        user_reports_json = c.fetchall()
        conn.close()
        
        user_reports = [json.loads(r[0]) for r in user_reports_json]
        
        if not user_reports:
            st.warning(f"{selected_name}さんのレポートは見つかりませんでした。")
        else:
            avg_balance = sum(r['deep_analysis']['balance_ratio'] for r in user_reports) / len(user_reports)
            avg_score = sum(r['deep_analysis']['success_score'] for r in user_reports) / len(user_reports)
            st.success(f"{len(user_reports)}件の商談データに基づき、フィードバックを生成しました。")
            col1, col2 = st.columns(2)
            col1.metric("平均会話バランス (営業担当)", f"{avg_balance:.1f}%")
            col2.metric("平均成功確度スコア", f"{avg_score:.1f} 点")
            st.info("次の目標: クロージングの際の、もう一歩踏み込んだ提案を練習しましょう。")
