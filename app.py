import os

from flask import Flask, request, jsonify, render_template, send_file
import joblib
import msal
import requests
import sqlite3
import re
import string
import pandas as pd
from datetime import datetime, timezone

import nltk
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer

MODEL_PATH = 'sentiment_model.joblib'
VECTORIZER_PATH = 'tfidf_vectorizer.joblib'
DB_FILE = 'sentiment_data.db'

POWERBI_TENANT_ID = os.environ.get('POWERBI_TENANT_ID')
POWERBI_CLIENT_ID = os.environ.get('POWERBI_CLIENT_ID')
POWERBI_CLIENT_SECRET = os.environ.get('POWERBI_CLIENT_SECRET')
POWERBI_WORKSPACE_ID = os.environ.get('POWERBI_WORKSPACE_ID')
POWERBI_DATASET_ID = os.environ.get('POWERBI_DATASET_ID')
POWERBI_REPORT_ID = os.environ.get('POWERBI_REPORT_ID')
POWERBI_DATASET_NAME = os.environ.get('POWERBI_DATASET_NAME', 'FlaskSentimentDataset')
POWERBI_TABLE_NAME = os.environ.get('POWERBI_TABLE_NAME', 'Reviews')

POWERBI_ENABLED = bool(
    POWERBI_TENANT_ID and POWERBI_CLIENT_ID and POWERBI_CLIENT_SECRET and POWERBI_WORKSPACE_ID
)

FALLBACK_STOPWORDS = {
    'a', 'an', 'the', 'and', 'or', 'but', 'if', 'then', 'so', 'to', 'of',
    'in', 'on', 'at', 'for', 'with', 'without', 'is', 'are', 'was', 'were',
    'be', 'been', 'being', 'i', 'you', 'he', 'she', 'it', 'we', 'they',
    'me', 'him', 'her', 'us', 'them', 'this', 'that', 'these', 'those',
    'my', 'your', 'his', 'its', 'our', 'their', 'do', 'does', 'did', 'not',
    'no', 'as', 'by', 'from', 'up', 'down', 'out', 'about', 'into', 'over',
    'again', 'further', 'than', 'too', 'very', 'can', 'will', 'just',
    'have', 'has', 'had', 'am', 'here', 'there', 'all', 'both', 'each',
    'more', 'most', 'other', 'some', 'such', 'only', 'own', 'same',
}


def _ensure_nltk_data():
    resources = [
        ('stopwords', 'corpora/stopwords'),
        ('wordnet', 'corpora/wordnet'),
        ('omw-1.4', 'corpora/omw-1.4'),
    ]
    for package, path in resources:
        try:
            nltk.data.find(path)
        except LookupError:
            try:
                nltk.download(package, quiet=True)
            except Exception as e:
                print(f"Could not download nltk package '{package}': {e}")


_ensure_nltk_data()

try:
    stop_words = set(stopwords.words('english'))
except LookupError:
    print("nltk stopwords unavailable, using built-in fallback list.")
    stop_words = FALLBACK_STOPWORDS

try:
    lemmatizer = WordNetLemmatizer()
    lemmatizer.lemmatize('testing')
except LookupError:
    print("nltk wordnet unavailable, disabling lemmatization.")
    lemmatizer = None

try:
    model = joblib.load(MODEL_PATH)
    tfidf_vectorizer = joblib.load(VECTORIZER_PATH)
    print("Model and vectorizer loaded successfully.")
except (FileNotFoundError, Exception) as e:
    print(f"Error loading model/vectorizer: {e}")
    model = None
    tfidf_vectorizer = None

app = Flask(__name__, template_folder='templates', static_folder='static')


def clean_text(text):
    text = str(text).lower()
    text = re.sub(r'http\S+|www\S+', '', text)
    text = re.sub(r'\d+', '', text)
    text = text.translate(str.maketrans('', '', string.punctuation))
    tokens = text.split()
    if lemmatizer is not None:
        tokens = [lemmatizer.lemmatize(w) for w in tokens if w not in stop_words]
    else:
        tokens = [w for w in tokens if w not in stop_words]
    return ' '.join(tokens)


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT,
            review_text TEXT,
            rating REAL,
            sentiment TEXT,
            sentiment_score REAL,
            word_count INTEGER,
            timestamp TEXT
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS powerbi_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()
    conn.close()


def add_review_to_db(review_text, sentiment_label, sentiment_score, product_name="API_SUBMITTED", rating=None):
    if rating is not None:
        try:
            if pd.isna(rating):
                rating = None
        except (TypeError, ValueError):
            pass

    conn = None
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reviews (product_name, review_text, rating, sentiment, sentiment_score, word_count, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """, (
            product_name, review_text, rating, sentiment_label,
            sentiment_score, len(review_text.split()), datetime.now(timezone.utc).isoformat()
        ))
        conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def get_meta_value(key):
    conn = sqlite3.connect(DB_FILE)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM powerbi_meta WHERE key = ?;", (key,))
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_meta_value(key, value):
    conn = sqlite3.connect(DB_FILE)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO powerbi_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
            (key, value)
        )
        conn.commit()
    finally:
        conn.close()


def get_powerbi_access_token():
    if not POWERBI_ENABLED:
        raise RuntimeError("Power BI integration is not configured.")
    authority = f"https://login.microsoftonline.com/{POWERBI_TENANT_ID}"
    client = msal.ConfidentialClientApplication(
        client_id=POWERBI_CLIENT_ID,
        client_credential=POWERBI_CLIENT_SECRET,
        authority=authority
    )
    result = client.acquire_token_for_client(scopes=["https://analysis.windows.net/powerbi/api/.default"])
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description") or result.get("error") or "Unable to acquire Power BI access token.")
    return result["access_token"]


def find_dataset_by_name(token, dataset_name):
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{POWERBI_WORKSPACE_ID}/datasets"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)
    if not response.ok:
        raise RuntimeError(f"Power BI list datasets failed: {response.status_code} {response.text}")
    datasets = response.json().get("value", [])
    for dataset in datasets:
        if dataset.get("name") == dataset_name:
            return dataset.get("id")
    return None


def ensure_powerbi_dataset():
    if not POWERBI_ENABLED:
        return None
    dataset_id = POWERBI_DATASET_ID or get_meta_value('powerbi_dataset_id')
    if dataset_id:
        return dataset_id

    token = get_powerbi_access_token()
    existing_dataset_id = find_dataset_by_name(token, POWERBI_DATASET_NAME)
    if existing_dataset_id:
        set_meta_value('powerbi_dataset_id', existing_dataset_id)
        return existing_dataset_id

    url = f"https://api.powerbi.com/v1.0/myorg/groups/{POWERBI_WORKSPACE_ID}/datasets?defaultRetentionPolicy=None"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    payload = {
        'name': POWERBI_DATASET_NAME,
        'defaultMode': 'Push',
        'tables': [
            {
                'name': POWERBI_TABLE_NAME,
                'columns': [
                    {'name': 'product_name', 'dataType': 'string'},
                    {'name': 'review_text', 'dataType': 'string'},
                    {'name': 'rating', 'dataType': 'double'},
                    {'name': 'sentiment', 'dataType': 'string'},
                    {'name': 'sentiment_score', 'dataType': 'double'},
                    {'name': 'word_count', 'dataType': 'Int64'},
                    {'name': 'timestamp', 'dataType': 'DateTime'},
                ]
            }
        ]
    }
    response = requests.post(url, json=payload, headers=headers)
    if not response.ok:
        raise RuntimeError(f"Power BI create dataset failed: {response.status_code} {response.text}")
    dataset_id = response.json().get('id')
    if dataset_id:
        set_meta_value('powerbi_dataset_id', dataset_id)
    return dataset_id


def push_review_rows_to_powerbi(rows):
    if not POWERBI_ENABLED or not rows:
        return False
    dataset_id = ensure_powerbi_dataset()
    if not dataset_id:
        return False
    token = get_powerbi_access_token()
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{POWERBI_WORKSPACE_ID}/datasets/{dataset_id}/tables/{POWERBI_TABLE_NAME}/rows"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    payload = {'rows': rows}
    response = requests.post(url, json=payload, headers=headers)
    if not response.ok:
        print(f"Power BI push rows failed: {response.status_code} {response.text}")
        return False
    return True


init_db()


@app.route('/', methods=['GET'])
def home():
    return render_template('index.html')


@app.route('/predict', methods=['POST'])
def predict_sentiment():
    if model is None or tfidf_vectorizer is None:
        return jsonify({'error': 'Model not loaded.'}), 500
    if not request.is_json:
        return jsonify({'error': 'Request must be JSON'}), 400

    data = request.get_json(silent=True) or {}
    review_text = data.get('review_text')
    if not review_text or not isinstance(review_text, str):
        return jsonify({'error': '"review_text" field is required and must be a string.'}), 400

    try:
        cleaned = clean_text(review_text)
        review_tfidf = tfidf_vectorizer.transform([cleaned])
        predicted_label = model.predict(review_tfidf)[0]
        probabilities = model.predict_proba(review_tfidf)[0]
        class_index = list(model.classes_).index(predicted_label)
        confidence = probabilities[class_index]

        add_review_to_db(review_text, str(predicted_label), float(confidence))

        return jsonify({
            'review': review_text,
            'predicted_sentiment': str(predicted_label),
            'confidence_score': float(confidence)
        }), 200
    except Exception as e:
        print(f"Prediction error: {e}")
        return jsonify({'error': 'Internal error during prediction.'}), 500


@app.route('/upload', methods=['POST'])
def upload_reviews():
    if model is None or tfidf_vectorizer is None:
        return jsonify({'error': 'Model not loaded.'}), 500
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided. Use form field name "file".'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Empty filename.'}), 400

    try:
        df = pd.read_csv(file)
    except Exception as e:
        return jsonify({'error': f'Could not read CSV: {e}'}), 400

    text_col = None
    for candidate in ['review_text', 'review', 'text', 'Review', 'Text']:
        if candidate in df.columns:
            text_col = candidate
            break
    if text_col is None:
        return jsonify({'error': 'CSV must contain a review text column (e.g. "review_text").'}), 400

    results = []
    powerbi_rows = []
    for _, row in df.iterrows():
        raw_value = row[text_col]
        if pd.isna(raw_value):
            continue
        review_text = str(raw_value)
        if not review_text.strip():
            continue
        try:
            cleaned = clean_text(review_text)
            review_tfidf = tfidf_vectorizer.transform([cleaned])
            predicted_label = model.predict(review_tfidf)[0]
            probabilities = model.predict_proba(review_tfidf)[0]
            class_index = list(model.classes_).index(predicted_label)
            confidence = float(probabilities[class_index])

            product_name = row.get('product_name', 'CSV_UPLOAD') if 'product_name' in df.columns else 'CSV_UPLOAD'
            if pd.isna(product_name):
                product_name = 'CSV_UPLOAD'
            rating = row.get('rating', None) if 'rating' in df.columns else None

            add_review_to_db(review_text, str(predicted_label), confidence, product_name=str(product_name), rating=rating)

            results.append({
                'review': review_text,
                'predicted_sentiment': str(predicted_label),
                'confidence_score': confidence
            })

            powerbi_rows.append({
                'product_name': str(product_name),
                'review_text': review_text,
                'rating': float(rating) if rating is not None and not pd.isna(rating) else None,
                'sentiment': str(predicted_label),
                'sentiment_score': confidence,
                'word_count': len(review_text.split()),
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
        except Exception as e:
            print(f"Row prediction error: {e}")
            continue

    powerbi_push_status = None
    if POWERBI_ENABLED:
        powerbi_push_status = push_review_rows_to_powerbi(powerbi_rows)

    return jsonify({
        'processed_count': len(results),
        'results': results,
        'powerbi_push_enabled': POWERBI_ENABLED,
        'powerbi_push_success': powerbi_push_status
    }), 200


@app.route('/stats', methods=['GET'])
def get_stats():
    conn = None
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT sentiment, COUNT(*) FROM reviews GROUP BY sentiment;")
        sentiment_counts = dict(cursor.fetchall())

        cursor.execute("SELECT product_name, sentiment, COUNT(*) FROM reviews GROUP BY product_name, sentiment;")
        rows = cursor.fetchall()
        category_breakdown = {}
        for product_name, sentiment, count in rows:
            if not product_name:
                product_name = 'Unknown'
            category_breakdown.setdefault(product_name, {})[sentiment] = count

        cursor.execute("SELECT DATE(timestamp) AS review_date, COUNT(*) FROM reviews GROUP BY review_date ORDER BY review_date;")
        daily_rows = cursor.fetchall()
        daily_counts = [{'date': row[0], 'count': row[1]} for row in daily_rows]

        cursor.execute("SELECT COUNT(*) FROM reviews;")
        total_reviews = cursor.fetchone()[0]
        return jsonify({
            'total_reviews': total_reviews,
            'sentiment_distribution': sentiment_counts,
            'category_breakdown': category_breakdown,
            'daily_counts': daily_counts
        }), 200
    except sqlite3.Error as e:
        return jsonify({'error': f'Database error: {e}'}), 500
    finally:
        if conn:
            conn.close()


@app.route('/about-model', methods=['GET'])
def about_model():
    if model is None:
        return jsonify({'error': 'Model not loaded.'}), 500
    try:
        params = model.get_params()
    except Exception:
        params = {}
    return jsonify({
        'algorithm': type(model).__name__,
        'vectorizer': type(tfidf_vectorizer).__name__ if tfidf_vectorizer is not None else None,
        'hyperparameters': params,
        'classes': [str(c) for c in model.classes_] if hasattr(model, 'classes_') else None,
        'vocabulary_size': len(tfidf_vectorizer.vocabulary_) if tfidf_vectorizer is not None else None,
    }), 200


@app.route('/health', methods=['GET'])
def health_check():
    if model is not None and tfidf_vectorizer is not None:
        return jsonify({'status': 'ok'}), 200
    return jsonify({'status': 'error', 'message': 'Model not loaded'}), 500


@app.route('/dashboard', methods=['GET'])
def dashboard():
    return render_template('dashboard.html')


@app.route('/download-db', methods=['GET'])
def download_db():
    return send_file(DB_FILE, as_attachment=True, download_name='sentiment_data.db', mimetype='application/x-sqlite3')


@app.route('/powerbi-report', methods=['GET'])
def powerbi_report():
    if not POWERBI_ENABLED:
        return jsonify({'error': 'Power BI integration is not configured.'}), 400
    embed_url = get_meta_value('powerbi_report_embed_url') or None
    return render_template('powerbi_report.html', embed_url=embed_url)


@app.route('/powerbi/meta', methods=['POST'])
def set_powerbi_meta():
    if not POWERBI_ENABLED:
        return jsonify({'error': 'Power BI integration is not configured.'}), 400
    data = request.get_json(silent=True) or {}
    report_embed_url = data.get('report_embed_url')
    if not report_embed_url:
        return jsonify({'error': 'report_embed_url is required.'}), 400
    set_meta_value('powerbi_report_embed_url', report_embed_url)
    return jsonify({'status': 'ok'}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
