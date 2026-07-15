# Dota 2 Match Analyzer: Architecture Plan

## 1. Цель проекта

Dota 2 Match Analyzer - веб-приложение для анализа профессиональных матчей Dota 2 и расчета вероятности победы команд.

Первый этап должен работать без ML: prediction строится на прозрачной формуле поверх сохраненных данных о командах, героях, матчах и статистике. Архитектура при этом сразу разделяет сбор данных, хранение, API, frontend и prediction engine, чтобы позже добавить ML-модель, draft prediction и live prediction без переписывания всего проекта.

## 2. Принципы архитектуры

- Сначала простая формула, потом ML.
- Prediction engine является отдельным модулем с единым интерфейсом.
- Backend не должен знать детали конкретной модели: формула, CatBoost, LightGBM или scikit-learn подключаются как разные реализации.
- Данные сохраняются в PostgreSQL как источник правды.
- Worker отвечает за ingestion, обновление статистики и offline-расчеты.
- Frontend получает только готовые API-контракты и не содержит бизнес-логику prediction.
- Все сервисы запускаются локально через Docker Compose.

## 3. Структура папок

```text
betboom/
  README.md
  README_ARCHITECTURE.md
  docker-compose.yml
  .env.example

  frontend/
    package.json
    next.config.js
    tsconfig.json
    src/
      app/
      components/
      features/
        matches/
        teams/
        predictions/
        drafts/
      lib/
        api/
        types/
      styles/

  backend/
    pyproject.toml
    alembic.ini
    app/
      main.py
      core/
        config.py
        database.py
        logging.py
      api/
        v1/
          router.py
          endpoints/
            health.py
            matches.py
            teams.py
            heroes.py
            predictions.py
            drafts.py
            live.py
      domain/
        matches/
        teams/
        heroes/
        predictions/
        drafts/
        live/
      schemas/
      repositories/
      services/
      prediction/
        base.py
        formula_engine.py
        feature_builder.py
        model_engine.py
      db/
        models.py
        migrations/
      tests/

  worker/
    pyproject.toml
    app/
      main.py
      jobs/
        ingest_matches.py
        ingest_teams.py
        ingest_heroes.py
        refresh_team_stats.py
        refresh_hero_stats.py
        calculate_match_features.py
      clients/
        opendota.py
        stratz.py
        datdota.py
      services/
      tests/

  ml/
    README.md
    datasets/
      .gitkeep
    notebooks/
      .gitkeep
    training/
      build_dataset.py
      train_model.py
      evaluate_model.py
    models/
      .gitkeep
    inference/
      loader.py
      predict.py

  infra/
    postgres/
      init.sql
    scripts/
      dev.sh
      migrate.sh
      seed.sh
```

## 4. Основные сервисы

### frontend

Next.js + TypeScript приложение.

Задачи:

- список матчей;
- страница матча;
- сравнение команд;
- отображение вероятности победы;
- объяснение факторов prediction;
- позже: draft analysis и live match dashboard.

Frontend не рассчитывает вероятность сам. Он вызывает backend API.

### backend

FastAPI приложение.

Задачи:

- REST API для frontend;
- доступ к PostgreSQL;
- orchestration prediction engine;
- валидация входных данных;
- агрегация данных для страниц;
- отдача объяснимых факторов prediction.

Backend должен иметь слой:

- `api` - HTTP endpoints;
- `schemas` - Pydantic DTO;
- `services` - бизнес-операции;
- `repositories` - SQL queries / ORM access;
- `prediction` - интерфейс и реализации prediction engine.

### db

PostgreSQL.

Задачи:

- хранение матчей, команд, игроков, героев;
- хранение draft данных;
- хранение статистических snapshot'ов;
- хранение prediction results;
- хранение feature values для будущего ML.

### worker

Python worker.

На первом этапе можно запускать вручную через CLI-команды или периодические задачи. Позже можно добавить Celery/RQ/Arq, если появится очередь.

Задачи:

- загрузка данных из внешних источников;
- обновление статистики команд и героев;
- расчет pre-match features;
- сохранение prediction snapshots;
- позже: подготовка ML dataset и live updates.

### ml

Папка для будущей ML-части. На MVP backend ее не использует.

Задачи позже:

- построение датасета;
- обучение моделей;
- оценка качества;
- versioned model artifacts;
- inference wrapper для backend.

## 5. Docker Compose

Локальный `docker-compose.yml` должен содержать:

- `frontend` - Next.js dev server;
- `backend` - FastAPI + Uvicorn;
- `worker` - Python worker container;
- `postgres` - PostgreSQL;
- опционально `pgadmin` или `adminer` для локальной разработки.

Минимальные порты:

- frontend: `http://localhost:3000`;
- backend: `http://localhost:8000`;
- postgres: `localhost:5432`.

Backend, worker и ML scripts должны использовать один `.env` контракт:

```text
DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/dota_analyzer
POSTGRES_DB=dota_analyzer
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
API_V1_PREFIX=/api/v1
PREDICTION_ENGINE=formula
```

## 6. Основные таблицы базы данных

### reference data

#### heroes

Справочник героев.

Поля:

- `id`;
- `external_id`;
- `name`;
- `localized_name`;
- `primary_attribute`;
- `attack_type`;
- `roles`;
- `created_at`;
- `updated_at`.

#### teams

Профессиональные команды.

Поля:

- `id`;
- `external_id`;
- `name`;
- `tag`;
- `region`;
- `logo_url`;
- `created_at`;
- `updated_at`.

#### players

Игроки.

Поля:

- `id`;
- `external_id`;
- `nickname`;
- `real_name`;
- `country`;
- `created_at`;
- `updated_at`.

#### team_players

История составов.

Поля:

- `id`;
- `team_id`;
- `player_id`;
- `position`;
- `started_at`;
- `ended_at`;
- `is_active`.

### match data

#### matches

Матчи или карты. На MVP лучше считать одну запись одной картой, потому что Dota 2 статистика обычно хранится по map/match id.

Поля:

- `id`;
- `external_match_id`;
- `league_id`;
- `series_id`;
- `radiant_team_id`;
- `dire_team_id`;
- `started_at`;
- `duration_seconds`;
- `patch`;
- `winner_side`;
- `winner_team_id`;
- `status`;
- `created_at`;
- `updated_at`.

#### match_players

Участники матча.

Поля:

- `id`;
- `match_id`;
- `team_id`;
- `player_id`;
- `hero_id`;
- `side`;
- `position`;
- `kills`;
- `deaths`;
- `assists`;
- `gold_per_min`;
- `xp_per_min`;
- `last_hits`;
- `denies`;
- `hero_damage`;
- `tower_damage`;
- `net_worth`;
- `level`;
- `items_json`.

#### drafts

Draft одной карты.

Поля:

- `id`;
- `match_id`;
- `team_id`;
- `side`;
- `sequence`;
- `action`;
- `hero_id`;
- `is_pick`;
- `is_ban`;
- `created_at`.

`action` может быть `pick` или `ban`. `sequence` хранит порядок в draft.

### stats and features

#### team_stats_snapshots

Агрегированная статистика команды на дату.

Поля:

- `id`;
- `team_id`;
- `calculated_at`;
- `patch`;
- `matches_count`;
- `win_rate`;
- `radiant_win_rate`;
- `dire_win_rate`;
- `avg_game_duration`;
- `avg_kills`;
- `avg_deaths`;
- `form_last_5`;
- `form_last_10`;
- `elo_rating`;
- `created_at`.

#### hero_stats_snapshots

Агрегированная статистика героя.

Поля:

- `id`;
- `hero_id`;
- `calculated_at`;
- `patch`;
- `matches_count`;
- `pick_rate`;
- `ban_rate`;
- `win_rate`;
- `avg_duration`;
- `created_at`.

#### match_features

Feature values, рассчитанные до prediction. Эта таблица важна для будущего ML.

Поля:

- `id`;
- `match_id`;
- `feature_version`;
- `features_json`;
- `created_at`.

Пример `features_json`:

```json
{
  "radiant_team_win_rate_30d": 0.58,
  "dire_team_win_rate_30d": 0.52,
  "radiant_form_last_10": 0.7,
  "dire_form_last_10": 0.5,
  "radiant_elo": 1640,
  "dire_elo": 1580,
  "patch": "7.36"
}
```

#### predictions

Сохраненные результаты prediction.

Поля:

- `id`;
- `match_id`;
- `prediction_type`;
- `engine_name`;
- `engine_version`;
- `feature_version`;
- `radiant_win_probability`;
- `dire_win_probability`;
- `confidence`;
- `explanation_json`;
- `created_at`.

`prediction_type`:

- `prematch`;
- `draft`;
- `live`.

### live data later

#### live_match_states

Состояние live match на конкретный момент.

Поля:

- `id`;
- `match_id`;
- `game_time_seconds`;
- `radiant_score`;
- `dire_score`;
- `radiant_gold`;
- `dire_gold`;
- `radiant_xp`;
- `dire_xp`;
- `radiant_tower_count`;
- `dire_tower_count`;
- `state_json`;
- `created_at`.

## 7. API endpoints

Все endpoints должны быть под `/api/v1`.

### health

```text
GET /api/v1/health
```

Ответ:

```json
{
  "status": "ok"
}
```

### heroes

```text
GET /api/v1/heroes
GET /api/v1/heroes/{hero_id}
GET /api/v1/heroes/{hero_id}/stats
```

### teams

```text
GET /api/v1/teams
GET /api/v1/teams/{team_id}
GET /api/v1/teams/{team_id}/matches
GET /api/v1/teams/{team_id}/stats
```

### matches

```text
GET /api/v1/matches
GET /api/v1/matches/{match_id}
GET /api/v1/matches/{match_id}/draft
GET /api/v1/matches/{match_id}/features
GET /api/v1/matches/{match_id}/prediction
```

Query params for `GET /matches`:

- `team_id`;
- `status`;
- `from`;
- `to`;
- `limit`;
- `offset`.

### predictions

```text
POST /api/v1/predictions/prematch
POST /api/v1/predictions/draft
POST /api/v1/predictions/live
GET /api/v1/predictions/{prediction_id}
```

MVP request for prematch:

```json
{
  "radiant_team_id": 1,
  "dire_team_id": 2,
  "patch": "7.36",
  "scheduled_at": "2026-06-14T12:00:00Z"
}
```

MVP response:

```json
{
  "prediction_type": "prematch",
  "engine_name": "formula",
  "engine_version": "formula-v1",
  "radiant_win_probability": 0.56,
  "dire_win_probability": 0.44,
  "confidence": 0.62,
  "factors": [
    {
      "name": "recent_form",
      "radiant_value": 0.7,
      "dire_value": 0.5,
      "impact": 0.08
    }
  ]
}
```

### worker/admin endpoints later

В MVP лучше не открывать ingestion наружу. Для локальной разработки можно использовать CLI внутри worker. Позже можно добавить защищенные admin endpoints:

```text
POST /api/v1/admin/ingest/matches
POST /api/v1/admin/stats/recalculate
POST /api/v1/admin/predictions/recalculate
```

## 8. Prediction engine

Prediction engine должен быть изолирован за интерфейсом.

### Базовый интерфейс

```python
class PredictionEngine:
    name: str
    version: str

    def predict(self, features: dict) -> PredictionResult:
        raise NotImplementedError
```

### Компоненты

#### FeatureBuilder

Отвечает за превращение данных из БД в стабильный набор признаков.

Пример MVP features:

- win rate команды за 30/60/90 дней;
- форма за последние 5 и 10 матчей;
- Elo-like rating;
- side advantage: Radiant/Dire;
- head-to-head win rate;
- средняя длительность игр;
- patch-specific performance;
- количество матчей в выборке.

FeatureBuilder должен иметь версию:

```text
feature_version = "prematch-v1"
```

Это позволит позже обучать ML на точно таких же признаках.

#### FormulaPredictionEngine

Первая реализация без ML.

Пример логики:

```text
score = 0.0
score += weight_form * form_delta
score += weight_win_rate * win_rate_delta
score += weight_elo * normalized_elo_delta
score += weight_side * radiant_side_bonus
score += weight_h2h * h2h_delta

radiant_probability = sigmoid(score)
dire_probability = 1 - radiant_probability
```

MVP веса можно хранить в config:

```json
{
  "form": 0.30,
  "win_rate": 0.25,
  "elo": 0.25,
  "side": 0.10,
  "head_to_head": 0.10
}
```

Важно: результат должен быть объяснимым. Engine возвращает не только вероятность, но и список факторов с вкладом каждого признака.

#### PredictionService

Слой backend, который:

1. принимает request;
2. вызывает FeatureBuilder;
3. выбирает engine по `PREDICTION_ENGINE`;
4. вызывает `engine.predict(features)`;
5. сохраняет `match_features`;
6. сохраняет `predictions`;
7. возвращает DTO frontend'у.

## 9. Как потом добавить ML

ML добавляется как новая реализация PredictionEngine, а не как переписывание backend.

### Шаги

1. Зафиксировать `feature_version`, например `prematch-v1`.
2. Наполнить таблицу `match_features` историческими матчами.
3. В `ml/training/build_dataset.py` собрать dataset:
   - features из `match_features`;
   - target из `matches.winner_team_id`.
4. Обучить модель:
   - baseline: LogisticRegression / RandomForest;
   - production candidate: CatBoost / LightGBM.
5. Сохранить model artifact:
   - `ml/models/prematch_model_v1.cbm`;
   - рядом metadata: feature list, metrics, training date.
6. Добавить `ModelPredictionEngine` в backend:
   - загружает model artifact;
   - проверяет feature_version;
   - вызывает `model.predict_proba`.
7. Переключить `.env`:

```text
PREDICTION_ENGINE=model
MODEL_PATH=/app/ml/models/prematch_model_v1.cbm
```

### Что важно не сломать

- FeatureBuilder должен быть общим для formula и ML.
- API response не должен меняться.
- `predictions.engine_name` должен показывать, чем сделан прогноз: `formula`, `catboost`, `lightgbm`.
- Все prediction results должны сохраняться с `engine_version`.

## 10. Как потом добавить draft prediction

Draft prediction - это отдельный `prediction_type = draft`, но он использует ту же общую архитектуру.

### Новые признаки

- выбранные герои Radiant;
- выбранные герои Dire;
- забаненные герои;
- synergy heroes внутри команды;
- counter matchups против героев соперника;
- comfort heroes игроков;
- team-specific hero win rate;
- patch hero strength;
- first pick / last pick;
- role completeness.

### Новые компоненты

```text
backend/app/prediction/draft_feature_builder.py
backend/app/prediction/draft_formula_engine.py
backend/app/prediction/draft_model_engine.py
```

MVP draft formula может использовать:

```text
draft_score =
  hero_patch_strength_delta +
  team_hero_comfort_delta +
  synergy_delta +
  counter_delta
```

### API

```text
POST /api/v1/predictions/draft
```

Request:

```json
{
  "radiant_team_id": 1,
  "dire_team_id": 2,
  "radiant_picks": [1, 2, 3, 4, 5],
  "dire_picks": [6, 7, 8, 9, 10],
  "radiant_bans": [11, 12, 13, 14, 15, 16, 17],
  "dire_bans": [18, 19, 20, 21, 22, 23, 24],
  "patch": "7.36"
}
```

Response должен быть таким же по форме, как prematch prediction, но с `prediction_type = draft`.

## 11. Как потом добавить live prediction

Live prediction - это `prediction_type = live`. Она использует live state, а не только pre-match данные.

### Источник данных

Возможные источники:

- Steam Web API, если доступно;
- STRATZ;
- OpenDota parsed live/pro data;
- manual ingest для MVP;
- replay/live state adapter позже.

### Live state features

- game time;
- net worth difference;
- XP difference;
- kill score;
- tower count;
- Roshan status;
- barracks status;
- buyback availability;
- hero levels;
- item timings;
- current draft;
- side;
- pre-match probability as prior.

### Поток данных

```text
external live source
  -> worker live ingest job
  -> live_match_states
  -> LiveFeatureBuilder
  -> LivePredictionEngine
  -> predictions
  -> frontend polling or websocket
```

### API

Polling MVP:

```text
GET /api/v1/live/matches
GET /api/v1/live/matches/{match_id}
GET /api/v1/live/matches/{match_id}/prediction
```

Later websocket:

```text
WS /api/v1/live/matches/{match_id}/stream
```

Websocket можно добавить после стабильного polling API. Frontend сначала должен уметь работать с обычным REST.

## 12. Data ingestion strategy

### MVP

Worker CLI jobs:

```text
python -m app.jobs.ingest_heroes
python -m app.jobs.ingest_teams
python -m app.jobs.ingest_matches --from 2025-01-01
python -m app.jobs.refresh_team_stats
python -m app.jobs.refresh_hero_stats
python -m app.jobs.calculate_match_features
```

### Источники данных

Приоритет:

1. OpenDota для доступных match details и hero data.
2. STRATZ, если нужен более полный pro/draft/live dataset.
3. DatDota/датасеты для исторического анализа, если API недостаточно.

Важно сделать clients в worker заменяемыми:

```text
worker/app/clients/opendota.py
worker/app/clients/stratz.py
worker/app/clients/datdota.py
```

## 13. Development roadmap

### Phase 1: project skeleton

- Docker Compose;
- FastAPI service;
- Next.js service;
- PostgreSQL;
- Alembic migrations;
- health endpoint;
- базовые таблицы.

### Phase 2: data MVP

- hero ingestion;
- team ingestion;
- match ingestion;
- team stats snapshots;
- hero stats snapshots.

### Phase 3: formula prediction

- FeatureBuilder `prematch-v1`;
- FormulaPredictionEngine `formula-v1`;
- endpoint `POST /predictions/prematch`;
- сохранение features and predictions;
- frontend match prediction UI.

### Phase 4: match analyzer UI

- список матчей;
- страница матча;
- сравнение команд;
- факторы прогноза;
- история прогнозов.

### Phase 5: ML-ready dataset

- заполнение `match_features` для исторических матчей;
- `ml/training/build_dataset.py`;
- baseline model offline;
- сравнение formula vs baseline.

### Phase 6: ML inference

- `ModelPredictionEngine`;
- model artifact loading;
- feature validation;
- engine switch through env;
- metrics tracking.

### Phase 7: draft prediction

- draft tables and API;
- DraftFeatureBuilder;
- draft formula;
- draft ML dataset later.

### Phase 8: live prediction

- live state ingestion;
- polling API;
- live prediction formula;
- websocket later;
- live ML model later.

## 14. MVP definition

MVP считается готовым, когда локально через Docker Compose можно:

1. поднять frontend, backend, worker и postgres;
2. применить migrations;
3. загрузить тестовые команды, героев и матчи;
4. открыть frontend;
5. выбрать две команды;
6. получить prematch win probability по формуле;
7. увидеть основные факторы, которые повлияли на прогноз;
8. найти сохраненный prediction в PostgreSQL.

## 15. Что не делать на первом этапе

- Не добавлять ML inference в production path.
- Не строить сложную live architecture.
- Не добавлять очередь задач, пока worker CLI достаточно.
- Не оптимизировать под большой трафик.
- Не смешивать prediction logic с frontend.
- Не хранить features только в Python объектах: они должны сохраняться для будущего ML.
- Не менять API response при переходе с formula на ML.
