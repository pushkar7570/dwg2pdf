# CAD Text Mover MVP

A local-first FastAPI service that:

1. accepts `DWG` / `DXF` / `DWF`,
2. sends the CAD file to CloudConvert,
3. downloads the converted PDF,
4. detects text overlapping drawing content,
5. relocates overlapping text into detected page margins,
6. emits a revised PDF and JSON audit data.

This is intentionally an MVP:

- low infrastructure cost,
- SQLite queue,
- local file storage,
- deterministic page analysis,
- API-first,
- async background processing,
- modular enough to split into separate API / worker services later.

---

## 1. Precise requirements

### Functional

- Accept one `DWG`, `DXF`, or `DWF` file per job.
- Create an asynchronous processing job immediately.
- Convert CAD input to PDF through CloudConvert.
- Extract text from the PDF, preferring native PDF text and only using OCR as fallback.
- Detect whether text overlaps drawing geometry.
- Move overlapping text outside the drawing area, preferring detected page margins.
- Remove or hide the original text.
- Save:
  - source converted PDF,
  - revised PDF,
  - audit JSON.
- Expose job status and artifact download endpoints.

### Non-functional

- Deterministic core logic.
- Minimal dependencies.
- No message broker for MVP.
- Works on a single machine.
- Easy to replace local storage and SQLite later.
- Easy to replace in-process worker with external worker later.

### Explicit out-of-scope for MVP

- Perfect CAD semantic understanding.
- True object-level editing inside DWG/DXF.
- ML-based label classification.
- Multi-tenant auth / permissions.
- Horizontal scaling / distributed locking.
- Guaranteed safe removal of rasterized text without visual trade-offs.

---

## 2. Architecture

```text
Client
  -> FastAPI API
       -> SQLite jobs table
       -> local storage
       -> in-process async worker loop
             -> CloudConvert API
             -> source PDF
             -> PDF processor
                   -> PyMuPDF text/vector extraction
                   -> OpenCV raster geometry extraction
                   -> Shapely overlap scoring
                   -> Tesseract fallback only when native text is absent
             -> revised PDF + audit JSON
```

### Modules

- `app/main.py` — FastAPI app and REST endpoints.
- `app/worker.py` — async job polling and processing loop.
- `app/cloudconvert_client.py` — CloudConvert REST client.
- `app/db.py` — SQLite repository.
- `app/storage.py` — local file path management.
- `app/pdf/text_extraction.py` — native text extraction plus OCR fallback.
- `app/pdf/geometry.py` — page rendering, content masks, drawing geometry, margin detection.
- `app/pdf/scoring.py` — overlap scoring, classification, confidence.
- `app/pdf/placement.py` — deterministic packing into page margins.
- `app/pdf/processor.py` — end-to-end PDF rewrite and audit generation.

### Why this architecture

- No broker required for MVP.
- Queue state survives process restarts because SQLite stores job state.
- PDF analysis is separated from API code.
- CloudConvert integration is isolated from PDF logic.
- Later migration path is straightforward:
  - replace SQLite with Postgres,
  - replace local storage with S3,
  - replace in-process worker with Celery / Dramatiq / Arq,
  - replace polling with CloudConvert webhook ingestion.

---

## 3. Deterministic core logic

The non-CloudConvert portion is deterministic because:

- thresholds are fixed or derived directly from page dimensions,
- margin detection is histogram-based,
- candidate placement uses deterministic strip scoring and scan order,
- OCR is only used when native text is missing,
- all move decisions are scored and recorded.

---

## 4. Overlap scoring formula

For each text item:

- `text_box` = original text bounding box.
- `expanded_box` = `text_box` expanded by `max(2 pt, 0.15 * font_size)`.
- `direct_overlap` = `max(ioa_geometry, ioa_mask)`.
- `expanded_density` = `max(expanded_geom_density, expanded_mask_density)`.
- `center_in_drawing` = `1` if the text center lies inside drawing geometry or on a drawing pixel, else `0`.

Where:

- `ioa_geometry = area(text_box ∩ drawing_union) / area(text_box)`
- `ioa_mask = drawing_pixels_inside(text_box) / total_pixels(text_box)`
- `expanded_geom_density = area(expanded_box ∩ drawing_union) / area(expanded_box)`
- `expanded_mask_density = drawing_pixels_inside(expanded_box) / total_pixels(expanded_box)`

Final score:

```text
overlap_score = 0.60 * direct_overlap
              + 0.25 * expanded_density
              + 0.15 * center_in_drawing
```

Decision thresholds:

- `>= 0.45` => move candidate
- `0.25 to < 0.45` => review
- `< 0.25` => keep

These thresholds are configurable in `.env`.

---

## 5. Geometry extraction strategy

Hybrid extraction is used because CAD-to-PDF output varies.

### Step A: native page render

- Render the PDF page with PyMuPDF at fixed DPI.
- Build a grayscale image.

### Step B: content mask with OpenCV

- Threshold near-white pixels out.
- Combine fixed threshold and adaptive threshold.
- Median blur for stability.

### Step C: text subtraction

- Extract text boxes first.
- Rasterize text boxes into a text mask.
- Subtract text mask from the content mask to isolate drawing content.

### Step D: denoise drawing mask

- Morphological close/open.
- Remove tiny connected components.

### Step E: contours to Shapely polygons

- Use `cv2.findContours()`.
- Convert contours into page-coordinate polygons.

### Step F: vector supplement

- Use PyMuPDF `get_drawings()` and `cluster_drawings()`.
- Add vector cluster boxes to the union geometry.

### Final drawing geometry

```text
drawing_union = union(raster_contour_polygons + vector_cluster_boxes)
```

This hybrid path handles:

- vector CAD output,
- mixed vector/raster output,
- lightly rasterized conversion output.

---

## 6. Text classification strategy

Text source priority:

1. native PDF text (`Page.get_text("rawdict")`)
2. OCR fallback only if native text count is below `OCR_FALLBACK_MIN_NATIVE_ITEMS`

Classification rules:

- `move`
  - overlap score >= move threshold,
  - not too long,
  - not strongly rotated,
  - not obviously title/header/title-block text.
- `review`
  - weak overlap,
  - low OCR/native confidence,
  - rotated text,
  - text too long,
  - no valid relocation slot.
- `keep`
  - outside drawing area,
  - likely header/footer/margin/title block,
  - low overlap.

Current heuristics flag these as likely non-move text:

- large title text near top of page,
- text already near page edges,
- text in the bottom-right title-block corner when overlap is low.

---

## 7. Page margin selection strategy

Margins are not guessed from a single drawing bounding box.

Instead, the processor measures drawing-density histograms from the raster drawing mask:

- per-column density,
- per-row density,
- smoothed with a fixed window.

For each edge (`left`, `right`, `top`, `bottom`):

- scan inward until `N` consecutive columns/rows exceed the configured density threshold,
- treat the low-density prefix as the margin strip.

This works better than simple bounding boxes because CAD sheets often have:

- border frames,
- title blocks,
- isolated edge geometry.

Only strips with size >= `MIN_MARGIN_SIZE_POINTS` are considered usable.

---

## 8. Relocation packing strategy

For each move candidate:

1. Score available strips:

```text
strip_score = 0.50 * free_area_ratio
            + 0.35 * proximity_to_source
            + 0.15 * same_edge_bonus
```

2. For each strip in descending score order:
   - fit text into strip width by deterministic wrapping,
   - search candidate positions on a fixed step grid,
   - reject any candidate that:
     - overlaps blockers,
     - lies outside strip,
     - overlaps drawing pixels.

3. First valid position in deterministic order wins.

### Blockers

- non-moved text,
- already placed relocation boxes.

### Placement rendering

Each relocated item is rendered as:

- white filled callout box,
- black outline,
- black text,
- leader line from original center to new box.

---

## 9. Redaction / removal strategy

### Native PDF text

- Add a redaction annotation over the original text box.
- Apply redactions with:
  - text removed,
  - images preserved,
  - line art preserved.

This is the preferred path.

### OCR-only fallback text

For OCR-detected text, the original text is usually rasterized or outlined, so text-only redaction is not reliable.

MVP strategy:

- draw a white rectangle over the original OCR box,
- then add the relocated callout.

This is intentionally flagged in audit output as a weaker path because it can cover nearby drawing strokes.

---

## 10. Confidence scoring and review flags

Final confidence for a moved item:

```text
final_confidence = 0.40 * extraction_confidence
                 + 0.35 * normalized_overlap_confidence
                 + 0.25 * placement_confidence
```

Where:

- `extraction_confidence`
  - `1.0` for native text,
  - OCR average confidence / 100 for OCR text.
- `normalized_overlap_confidence = min(1, overlap_score / 0.80)`
- `placement_confidence = 1 - min(1, move_distance / page_diagonal)`

Automatic review flags include:

- `ocr_used`
- `low_extraction_confidence`
- `rotated_text`
- `text_too_long`
- `large_title_text`
- `margin_or_header_text`
- `title_block_text`
- `weak_overlap`
- `no_margin_slot`
- `manual_review_recommended`

A moved item with final confidence `< 0.65` is marked for manual review.

---

## 11. Failure modes

### Input / conversion

- invalid extension
- corrupt upload
- CloudConvert auth failure
- CloudConvert conversion failure
- unsupported CAD features / layouts
- download URL expired before retrieval

### Extraction / geometry

- CAD conversion rasterizes everything
- text converted to curves instead of text objects
- OCR misses tiny labels
- OCR box includes nearby lines
- border/title block geometry reduces available margin space

### Rewrite

- no strip large enough for relocation
- visual whiteout hides nearby line art on OCR-only pages
- Base-14 replacement font differs from original CAD font
- wrapped callout changes document aesthetics

### Operational

- single-process worker means limited throughput
- SQLite is adequate for MVP, not for large concurrent workloads
- local disk is not suitable for multi-node deployment

---

## 12. Run locally

### Prerequisites

- Python 3.11+
- Tesseract installed and available on `PATH`
- At least one CloudConvert API key

### Setup

```bash
cd cad_text_mover_mvp
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and set either CLOUDCONVERT_API_KEY
# or CLOUDCONVERT_API_KEYS=primary,secondary
```

### Start API + in-process worker

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Open the frontend

Open `http://localhost:8000` in a browser.

The frontend is a no-build static page served by FastAPI. It supports upload, live status polling, progress display, and final output PDF download.

### CloudConvert key failover

You can configure one or more CloudConvert API keys.

Single-key setup:

```env
CLOUDCONVERT_API_KEY=your-primary-key
```

Automatic failover setup:

```env
CLOUDCONVERT_API_KEYS=your-primary-key,your-secondary-key
CLOUDCONVERT_FAILOVER_ENABLED=true
CLOUDCONVERT_KEY_COOLDOWN_SECONDS=900
```

Behavior:

- keys are tried in priority order,
- if job creation hits account capacity, quota, auth-unavailable, or create-job rate limiting, the client automatically retries the same conversion on the next configured key,
- polling rate limits on an already-created job stay on the same key and honor `Retry-After`,
- if all configured keys are unavailable for capacity-related reasons, the API returns a generic provider-unavailable error instead of exposing the first-key failure to the end user.

### Optional: separate worker process later

The code already allows a standalone worker entrypoint:

```bash
python -m app.worker_main
```

For the MVP you do not need this because the FastAPI lifespan starts the worker automatically.

---

## 13. API

### Create job

```bash
curl -X POST "http://localhost:8000/v1/jobs" \
  -F "file=@sample.dwg" \
  -F 'cloudconvert_options={"all_layouts":true}'
```

Sample response:

```json
{
  "id": "1f8d66fe-e53c-44d5-9efd-6db00fe3fca6",
  "status": "queued",
  "input_filename": "sample.dwg",
  "created_at": "2026-04-19T10:15:24.225631+00:00",
  "links": {
    "self": "http://localhost:8000/v1/jobs/1f8d66fe-e53c-44d5-9efd-6db00fe3fca6",
    "audit": "http://localhost:8000/v1/jobs/1f8d66fe-e53c-44d5-9efd-6db00fe3fca6/audit",
    "source_pdf": null,
    "output_pdf": null
  }
}
```

### Poll job

```bash
curl "http://localhost:8000/v1/jobs/1f8d66fe-e53c-44d5-9efd-6db00fe3fca6"
```

Sample completed response:

```json
{
  "id": "1f8d66fe-e53c-44d5-9efd-6db00fe3fca6",
  "status": "completed",
  "input_filename": "sample.dwg",
  "created_at": "2026-04-19T10:15:24.225631+00:00",
  "started_at": "2026-04-19T10:15:25.101442+00:00",
  "finished_at": "2026-04-19T10:15:31.500912+00:00",
  "error_message": null,
  "metrics": {
    "pages": 1,
    "moved_text_count": 4,
    "review_item_count": 1,
    "ocr_pages": 0,
    "cloudconvert_credits": 1,
    "cloudconvert_api_key_slot": 1,
    "cloudconvert_api_keys_tried": 1,
    "cloudconvert_failover_used": false
  },
  "links": {
    "self": "http://localhost:8000/v1/jobs/1f8d66fe-e53c-44d5-9efd-6db00fe3fca6",
    "audit": "http://localhost:8000/v1/jobs/1f8d66fe-e53c-44d5-9efd-6db00fe3fca6/audit",
    "source_pdf": "http://localhost:8000/v1/jobs/1f8d66fe-e53c-44d5-9efd-6db00fe3fca6/source.pdf",
    "output_pdf": "http://localhost:8000/v1/jobs/1f8d66fe-e53c-44d5-9efd-6db00fe3fca6/output.pdf"
  }
}
```

### Get audit JSON

```bash
curl "http://localhost:8000/v1/jobs/1f8d66fe-e53c-44d5-9efd-6db00fe3fca6/audit"
```

Sample excerpt:

```json
{
  "input_pdf": "/.../converted.pdf",
  "output_pdf": "/.../revised.pdf",
  "summary": {
    "pages": 1,
    "moved_text_count": 4,
    "review_item_count": 1,
    "ocr_pages": 0
  },
  "pages": [
    {
      "page_number": 0,
      "native_text_items": 14,
      "ocr_text_items": 0,
      "drawing_component_count": 9,
      "margin_strips": {
        "left": {"x0": 0, "y0": 0, "x1": 28.0, "y1": 842.0},
        "right": {"x0": 566.0, "y0": 0, "x1": 595.0, "y1": 842.0}
      },
      "move_records": [
        {
          "moved": true,
          "redaction_strategy": "native_redaction",
          "final_confidence": 0.92,
          "text_item": {
            "text": "VALVE A",
            "classification": "move",
            "overlap_score": 0.78,
            "review_flags": []
          },
          "placement": {
            "strip_name": "right",
            "target_bbox": {"x0": 566.0, "y0": 122.0, "x1": 593.0, "y1": 138.0},
            "placement_confidence": 0.84,
            "render_text": "VALVE A",
            "render_font_size": 11.5
          }
        }
      ]
    }
  ]
}
```

---

## 14. Tests

A smoke test is included which builds a synthetic PDF and verifies that overlapping text is moved to a margin.

```bash
pytest -q tests/test_processor_smoke.py
```

---

## 15. Weak points

1. OCR-only pages are the weakest path.
   - Whiteout can hide line art.
   - OCR confidence for tiny CAD labels can be poor.

2. Margin discovery is sheet-layout aware, but not CAD-semantic.
   - It does not know what a title block or viewport truly is.

3. Replacement typography is approximate.
   - The MVP uses Helvetica (`helv`) for relocated text.

4. Some CAD-to-PDF conversions flatten semantics.
   - If CloudConvert emits outlines or raster only, perfect original-text removal is impossible without destructive image edits.

5. In-process worker is simple but not scalable.

---

## 16. Future improvements

- Use CloudConvert webhooks instead of polling.
- Run API and worker separately.
- Replace SQLite with Postgres.
- Replace local storage with S3 / GCS.
- Add explicit title-block detection.
- Add viewport detection and per-viewport relocation zones.
- Add overflow relocation page when no margin slot exists.
- Add font preservation and leader-line routing.
- Add batch uploads and job priorities.
- Add auth, retention policies, and signed download URLs.
- Add richer audit visualization overlays.

