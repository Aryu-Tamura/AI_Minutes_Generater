# AI議事録アシスタント

商談などの音声データをアップロードすると、AIが自動で文字起こし、議事録作成、多角的な分析を行うStreamlitアプリケーションです。

## 主な機能

-   **話者分離付き文字起こし**: `pyannote.audio`と`Whisper`を使用し、誰が何を話したかをタイムスタンプ付きで記録します。
-   **AIによる議事録・分析レポート生成**: `GPT-4o`が会話内容を分析し、議事録とAIコーチングレポートを自動で作成します。
-   **対話によるレポート編集**: チャット形式でAIに指示を出し、生成されたレポートを対話的に修正できます。
-   **レポートの永続化**: 作成したレポートはSQLiteデータベースに保存され、いつでも閲覧・再編集が可能です。

## セットアップ手順

1.  **リポジトリをクローン**
    ```bash
    git clone https://github.com/Aryu-Tamura/AI_Minutes_Generater.git
    cd AI_Minutes_Generater
    ```

2.  **仮想環境の作成と有効化**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **必要なライブラリのインストール**
    ```bash
    pip install -r requirements.txt
    ```

4.  **APIキーの設定**
    プロジェクト内に`.streamlit`というフォルダを作成し、その中に`secrets.toml`というファイルを作成してください。ファイルには以下の内容を記述します。

    ```toml
    # .streamlit/secrets.toml
    HF_TOKEN = "YOUR_HUGGINGFACE_TOKEN"
    OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
    ```
    *`HF_TOKEN`は[Hugging Face](https://huggingface.co/settings/tokens)で取得してください。*
    *`pyannote/speaker-diarization-3.1`と`pyannote/segmentation-3.0`の利用規約への同意が必要です。*

## 実行方法

以下のコマンドを実行すると、Webブラウザでアプリケーションが起動します。

```bash
streamlit run app.py
