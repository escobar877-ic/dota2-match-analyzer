# Lightweight Local ML Constraints

This project is local-first. ML must be able to run on a normal MacBook with 16GB RAM and no dedicated GPU.

## Required Rules

- ML runs locally only.
- ML is CPU-first.
- Cloud ML services are not allowed.
- Neural networks are not allowed at the start.
- PyTorch and TensorFlow are not allowed.
- OpenAI API, HuggingFace API, Google Vertex AI, AWS ML services, Supabase-hosted ML, and other external ML services are not allowed.
- Training data must come only from local PostgreSQL.
- Training data must use only Tier 1 matches.
- Model artifacts must be stored locally in `ml/artifacts/`.
- If an ML model is missing, invalid, or fails, the app must use the formula/Elo fallback.

## Allowed Starting Models

- Logistic Regression
- Random Forest

## Later Candidates

LightGBM or CatBoost can be considered later only if they remain lightweight, local, CPU-first, and optional.
