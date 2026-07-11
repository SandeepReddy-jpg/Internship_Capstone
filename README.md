# Internship Capstone

This repository contains a Flask-based sentiment analysis web application.

## Project contents

- `app.py` - Flask application entry point
- `requirements.txt` - Python dependencies
- `sentiment_model.joblib` - trained sentiment classification model
- `tfidf_vectorizer.joblib` - TF-IDF vectorizer used for feature extraction
- `templates/` - HTML templates for the web UI
- `static/` - client-side JavaScript assets

## Setup

1. Create and activate a Python virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

## Run the app

```powershell
python app.py
```

Then open the browser at `http://127.0.0.1:5000`.

## Features

- Upload CSV files for sentiment processing
- Review summary statistics and live dashboard charts
- Download sentiment results and view Power BI helper endpoints

## Notes

- The local GitHub repository is linked to `https://github.com/SandeepReddy-jpg/Internship_Capstone.git`
- Exclude generated files and virtual environments using `.gitignore`
