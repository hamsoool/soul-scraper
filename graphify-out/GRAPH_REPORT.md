# Graph Report - soul-scrape  (2026-09-24)

## Corpus Check
- Corpus is ~7,148 words - fits in a single context window. You may not need a graph.

## Summary
- 183 nodes · 300 edges · 10 communities (9 shown, 1 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 39 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Stack & Deployment Config
- PDF Scraping & Price Parsing
- Scheduler & Sync Pipeline
- Document API & Models
- Fetch & Ingestion Helpers
- API Schemas & Sync Trigger
- Security & SSRF Protection
- Documented API Endpoints
- Configuration Settings
- Environment Dependencies

## God Nodes (most connected - your core abstractions)
1. `sync_doe_data()` - 14 edges
2. `Document` - 13 edges
3. `Sync Ingestion Pipeline (sync.py)` - 12 edges
4. `scrape_source_page()` - 9 edges
5. `API Key Authentication` - 8 edges
6. `extract_pdf_content()` - 7 edges
7. `FastAPI REST API` - 7 edges
8. `lifespan()` - 6 edges
9. `trigger_sync()` - 6 edges
10. `get_stats()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `fastapi (dependency)` --semantically_similar_to--> `FastAPI REST API`  [INFERRED] [semantically similar]
  requirements.txt → README.md
- `doe-scraper-db PostgreSQL Database` --semantically_similar_to--> `PostgreSQL (Supabase)`  [INFERRED] [semantically similar]
  render.yaml → README.md
- `sqlalchemy (dependency)` --semantically_similar_to--> `SQLAlchemy 2.0`  [INFERRED] [semantically similar]
  requirements.txt → README.md
- `httpx (dependency)` --semantically_similar_to--> `HTTPX`  [INFERRED] [semantically similar]
  requirements.txt → README.md
- `beautifulsoup4 (dependency)` --semantically_similar_to--> `BeautifulSoup4`  [INFERRED] [semantically similar]
  requirements.txt → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Document Ingestion Pipeline** — readme_sync_ingestion_pipeline, readme_httpx, readme_beautifulsoup4, readme_pymupdf, readme_apscheduler, readme_postgresql_supabase [EXTRACTED 1.00]
- **Render Deployment Configuration** — render_doe_pdf_aggregator, render_doe_pdf_aggregator_cron, render_doe_scraper_db, render_dockerfile [EXTRACTED 1.00]

## Communities (10 total, 1 thin omitted)

### Community 0 - "Stack & Deployment Config"
Cohesion: 0.06
Nodes (37): APScheduler, Async Architecture, BeautifulSoup4, FastAPI REST API, Free-tier Render Wake-up Latency Rationale, HTTPX, POST /sync, PostgreSQL (Supabase) (+29 more)

### Community 1 - "PDF Scraping & Price Parsing"
Cohesion: 0.07
Nodes (31): clean_cell(), extract_pdf_content(), extract_pdf_text(), extract_pdf_text_sync(), extract_price_adjustments_sync(), extract_prices_from_pdf_ocr(), extract_prices_from_pdf_sync(), parse_float() (+23 more)

### Community 2 - "Scheduler & Sync Pipeline"
Cohesion: 0.11
Nodes (28): get_db(), AsyncSession, Dependency for providing database sessions to endpoints., lifespan(), Wrapper task for manual background sync., run_manual_sync(), execute_sync_job(), Scheduled task wrapper that creates a DB session and runs the sync. (+20 more)

### Community 3 - "Document API & Models"
Cohesion: 0.14
Nodes (18): get_document(), get_latest_documents(), get_stats(), health_check(), list_documents(), AsyncSession, Retrieves the single most recent document for each of the source categories., Returns diagnostics and summary statistics about the database and scraper runs. (+10 more)

### Community 4 - "Fetch & Ingestion Helpers"
Cohesion: 0.15
Nodes (15): download_pdf_stream(), fetch_with_backoff(), parse_date_from_pdf_url(), parse_date_from_text(), parse_nuxt_state(), Extracts the start date from a DOE pump price PDF URL filename. Strategy:…, Parses a Nuxt state block and extracts (parameters, body_str, arguments_list)., Fetches a URL with exponential backoff retry mechanism. (+7 more)

### Community 5 - "API Schemas & Sync Trigger"
Cohesion: 0.24
Nodes (11): Triggers the DOE website scraper manually in the background without blocking…, trigger_sync(), DocumentBase, DocumentCreate, DocumentListItem, DocumentRead, StatsResponse, SyncResponse (+3 more)

### Community 6 - "Security & SSRF Protection"
Cohesion: 0.18
Nodes (11): is_internal_ip(), FastAPI dependency that validates the X-API-Key header. Uses…, Check if an IP address belongs to loopback, private or local ranges., Validates that a URL is: 1. A valid URL with scheme HTTPS. 2. Belongs strictly…, validate_and_resolve_url(), verify_api_key(), fastapi_security, ipaddress (+3 more)

### Community 7 - "Documented API Endpoints"
Cohesion: 0.28
Nodes (9): API Key Authentication, Document Categories, Aggregated Document, GET /documents/{id}, GET /documents, GET /health, GET /latest, GET /stats (+1 more)

### Community 8 - "Configuration Settings"
Cohesion: 0.25
Nodes (6): Converts standard postgres:// or postgresql:// URLs to postgresql+asyncpg:// if…, Settings, BaseSettings, os, pydantic, pydantic_settings

## Ambiguous Edges - Review These
- `numpy (dependency)` → `Pillow (dependency)`  [AMBIGUOUS]
  requirements.txt · relation: conceptually_related_to

## Knowledge Gaps
- **18 isolated node(s):** `GET /health`, `GET /stats`, `Docker / Render Deployment`, `fastapi (dependency)`, `uvicorn (dependency)` (+13 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 85 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `numpy (dependency)` and `Pillow (dependency)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `Document` connect `Document API & Models` to `PDF Scraping & Price Parsing`, `Scheduler & Sync Pipeline`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **Why does `sync_doe_data()` connect `Scheduler & Sync Pipeline` to `PDF Scraping & Price Parsing`, `Document API & Models`, `Fetch & Ingestion Helpers`?**
  _High betweenness centrality (0.041) - this node is a cross-community bridge._
- **Why does `Sync Ingestion Pipeline (sync.py)` connect `Stack & Deployment Config` to `Documented API Endpoints`?**
  _High betweenness centrality (0.040) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `Document` (e.g. with `get_document()` and `get_latest_documents()`) actually correct?**
  _`Document` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `Sync Ingestion Pipeline (sync.py)` (e.g. with `Streamed Downloads with File Size Limits` and `SYNC_INTERVAL_HOURS (24)`) actually correct?**
  _`Sync Ingestion Pipeline (sync.py)` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `GET /health`, `GET /stats`, `Docker / Render Deployment` to the rest of the system?**
  _18 weakly-connected nodes found - possible documentation gaps or missing edges._