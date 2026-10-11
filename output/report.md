# pSEO run report: run #8

- **Status:** ok
- **Finished:** 2026-10-11 08:21:51 UTC
- **Duration:** 1.0 s
- **Provider:** gemini (writer `gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-2.5-flash`, embeddings `gemini-embedding-001`)
- **LLM usage:** 0 generate calls {}, 1 embed calls, 0 input / 0 output tokens, 0 retries
- **Counts:** planned=0, written=0, revisions=0, passed=0, quarantined=0, failed=0, skipped_unchanged=12, merged=0, retired=0, sweep_requeued=0
- **Published pages in corpus:** 12

## Keyword clustering (cannibalisation check before writing)

- No keyword pair above the merge threshold (0.96): every keyword gets its own page.
- Sibling pairs planned together and explicitly differentiated:
  - /scan-receipt-on-iphone ↔ /ocr-handwriting-iphone (cosine 0.892)
  - /scan-receipt-on-iphone ↔ /scan-passport-photo-for-application (cosine 0.855)
  - /scan-receipt-on-iphone ↔ /scan-business-card-to-contacts (cosine 0.854)
  - /convert-pdf-to-word-free ↔ /compress-pdf-file-size-free (cosine 0.899)
  - /convert-pdf-to-word-free ↔ /best-free-pdf-scanner-app (cosine 0.864)
  - /convert-pdf-to-word-free ↔ /sign-pdf-on-iphone-free (cosine 0.863)
  - /scan-document-without-an-app ↔ /scan-passport-photo-for-application (cosine 0.867)
  - /scan-document-without-an-app ↔ /convert-scanned-image-to-text (cosine 0.867)
  - /ocr-handwriting-iphone ↔ /convert-scanned-image-to-text (cosine 0.872)
  - /ocr-handwriting-iphone ↔ /sign-pdf-on-iphone-free (cosine 0.861)
  - /best-free-pdf-scanner-app ↔ /sign-pdf-on-iphone-free (cosine 0.867)
  - /best-free-pdf-scanner-app ↔ /compress-pdf-file-size-free (cosine 0.855)
  - /combine-multiple-pdfs-into-one ↔ /compress-pdf-file-size-free (cosine 0.855)
  - /combine-multiple-pdfs-into-one ↔ /convert-scanned-image-to-text (cosine 0.808)
  - /scan-passport-photo-for-application ↔ /convert-scanned-image-to-text (cosine 0.854)
  - /remove-shadow-from-scanned-document ↔ /convert-scanned-image-to-text (cosine 0.855)

## Duplicate-content metrics across all published pages

| Metric | Max pair | Mean | Threshold |
|---|---|---|---|
| 5-word shingle Jaccard | 0.0176 | 0.0058 | 0.12 |
| Embedding cosine | 0.9292 | 0.8501 | 0.93 |
| Verbatim shared sentences (≥8 words) | 1 | 0.02 | 1 |
| FAQ question cosine (across pages) | 0.9279 | 0.7592 | 0.93 |

Most similar pairs:

| Page A | Page B | Cosine | Jaccard | Shared sentences |
|---|---|---|---|---|
| convert-scanned-image-to-text | ocr-handwriting-iphone | 0.9292 | 0.0052 | 0 |
| best-free-pdf-scanner-app | scan-document-without-an-app | 0.9114 | 0.0115 | 0 |
| best-free-pdf-scanner-app | convert-pdf-to-word-free | 0.8987 | 0.0100 | 0 |
| convert-pdf-to-word-free | convert-scanned-image-to-text | 0.8966 | 0.0135 | 0 |
| scan-document-without-an-app | scan-receipt-on-iphone | 0.8941 | 0.0068 | 0 |
| scan-document-without-an-app | sign-pdf-on-iphone-free | 0.8878 | 0.0031 | 0 |
| best-free-pdf-scanner-app | sign-pdf-on-iphone-free | 0.8811 | 0.0076 | 0 |
| best-free-pdf-scanner-app | combine-multiple-pdfs-into-one | 0.8808 | 0.0139 | 0 |

## Pages

Closest neighbour = the most similar other published page in the final corpus.

| Page | Status | Words | Drafts | Editor (spec/use/acc/swap) | Max Jaccard | Max cosine (closest page) |
|---|---|---|---|---|---|---|
| best-free-pdf-scanner-app | passed | 1118 | 1 | 5/5/5/pass | 0.0176 | 0.9114 (scan-document-without-an-app) |
| combine-multiple-pdfs-into-one | passed | 1027 | 2 | 5/5/4/pass | 0.0151 | 0.8808 (best-free-pdf-scanner-app) |
| compress-pdf-file-size-free | passed | 1102 | 1 | 5/5/5/pass | 0.0176 | 0.8727 (combine-multiple-pdfs-into-one) |
| convert-pdf-to-word-free | passed | 1078 | 1 | 5/5/5/pass | 0.0135 | 0.8987 (best-free-pdf-scanner-app) |
| convert-scanned-image-to-text | passed | 1080 | 1 | 5/5/5/pass | 0.0135 | 0.9292 (ocr-handwriting-iphone) |
| ocr-handwriting-iphone | passed | 1282 | 2 | 5/5/5/pass | 0.0097 | 0.9292 (convert-scanned-image-to-text) |
| remove-shadow-from-scanned-document | passed | 1037 | 2 | 5/5/5/pass | 0.0149 | 0.8687 (best-free-pdf-scanner-app) |
| scan-business-card-to-contacts | passed | 1027 | 1 | 5/5/5/pass | 0.0061 | 0.8712 (scan-receipt-on-iphone) |
| scan-document-without-an-app | passed | 960 | 1 | 5/5/4/pass | 0.0115 | 0.9114 (best-free-pdf-scanner-app) |
| scan-passport-photo-for-application | passed | 1112 | 2 | 5/4/4/pass | 0.0084 | 0.8427 (scan-receipt-on-iphone) |
| scan-receipt-on-iphone | passed | 996 | 1 | 5/5/5/pass | 0.0076 | 0.8941 (scan-document-without-an-app) |
| sign-pdf-on-iphone-free | passed | 1059 | 1 | 5/5/5/pass | 0.0102 | 0.8878 (scan-document-without-an-app) |

## Rejections, revisions and incidents (all runs)

Each QA line is a draft that was rejected; its errors were sent back to the writer as revision instructions, and the page's later attempt is the one that was published. Run-level lines are incidents the pipeline handled itself.

**Run #1** (crashed, started 2026-10-11 06:41 UTC)

- **ocr-handwriting-iphone** [qa/warning]: attempt 1: editor scores: specificity 5, usefulness 5, accuracy 3, swap test pass; editor: The phrase "As a student, you often take lecture notes by hand." is filler and makes an assumption about the user.; editor: The instruction "tap the OCR button" invents a UI element. The app facts state that OCR "Recognises printed text on scans and photos and lets you copy it or export it as .txt" but do not specify a button name or menu path.
- **combine-multiple-pdfs-into-one** [qa/warning]: attempt 1: 2 sentences appear verbatim on other pages; rewrite them: [best-free-pdf-scanner-app] free tiers often limit file size or daily use | [convert-pdf-to-word-free] open document scanner pdf toolkit on your iphone or ipad
- **scan-passport-photo-for-application** [qa/warning]: attempt 1: meta_description is 169 chars, needs 110-160; over-claim 'guaranteed' - remove or qualify it
- **remove-shadow-from-scanned-document** [qa/warning]: attempt 1: editor scores: specificity 5, usefulness 4, accuracy 3, swap test pass; editor: Step 3: "Tap the 'Enhance' or 'Filter' option." The APP FACTS describe "One-tap filters" which implies direct selection, not an invented intermediate menu name like 'Enhance' or 'Filter'.; editor: Feature point for 'auto_edge': "even if shadows distort the background". The APP FACTS for 'auto_edge' do not support the claim that automatic edge detection works accurately even when shadows distort the background.; editor: Feature point for 'batch_scan': "applying the Shadow Removal filter consistently across all pages of a single document." This claim is not supported by APP FACTS and contradicts FAQ 5, w
- **scan-business-card-to-contacts** [write/error]: pseo.llm.QuotaExhausted: write/gemini-2.5-flash: daily quota exhausted
- **sign-pdf-on-iphone-free** [write/error]: pseo.llm.QuotaExhausted: all models exhausted: ['gemini-3.8-flash', 'gemini-3.5-flash', 'gemini-2.5-flash']
- **convert-scanned-image-to-text** [write/error]: pseo.llm.QuotaExhausted: all models exhausted: ['gemini-3.8-flash', 'gemini-3.5-flash', 'gemini-2.5-flash']
- **run** [run/error]: RuntimeError: 3 consecutive page failures - aborting run (circuit breaker); last error: QuotaExhausted: all models exhausted: ['gemini-3.8-flash', 'gemini-3.5-flash', 'gemini-2.5-flash']

**Run #3** (ok, started 2026-10-11 07:03 UTC)

- **convert-scanned-image-to-text** [qa/warning]: attempt 1: semantically too close to /ocr-handwriting-iphone (cosine 0.932 > 0.93); lean harder into this page's unique angle
- **convert-scanned-image-to-text** [qa/warning]: attempt 2: semantically too close to /ocr-handwriting-iphone (cosine 0.931 > 0.93); lean harder into this page's unique angle

**Run #5** (ok, started 2026-10-11 07:18 UTC)

- **convert-scanned-image-to-text** [qa/warning]: attempt 1: semantically too close to /ocr-handwriting-iphone (cosine 0.935 > 0.93). That page already covers: How to OCR Handwritten Notes on iPhone / Convert Handwritten Notes to Text on iPhone / Other Ways to Digitize Handwritten Text on iPhone / Tips for Better Handwriting OCR Results. Do not re-explain what it explains; mention it in one sentence and link. Rebuild the steps, tips and FAQs around this page's own angle: Step-by-step extraction of clean, printed text from images to plain text (.txt) or clipboard via free on-device processing, explaining the difference between extracting raw text and formatting it as a Word document.
- **convert-scanned-image-to-text** [qa/warning]: attempt 2: banned filler phrase: 'seamless'

**Run #6** (ok, started 2026-10-11 07:21 UTC)

- **convert-scanned-image-to-text** [sweep/warning]: sweep: too similar to /ocr-handwriting-iphone (jaccard 0.005, shared sentences 0, cosine 0.924); FAQ 'Can you export the extracted text as a .txt file or copy it to the clipboard for free?' asks the same thing as /ocr-handwriting-iphone's 'Can you copy or export recognized handwriting as plain .txt for free?' (cosine 0.948); replace it with a question specific to this page; FAQ 'How does on-device OCR compare to using built-in iOS Live Text in the Photos app?' asks the same thing as /ocr-handwriting-iphone's 'How does built-in iOS Live Text perform on handwriting compared to dedicated app OCR?' (cosine 0.949); replace it with a question specific to this page

