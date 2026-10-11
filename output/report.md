# pSEO run report: run #9

- **Status:** ok
- **Finished:** 2026-10-11 09:22:07 UTC
- **Duration:** 232.2 s
- **Provider:** gemini (writer `gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-2.5-flash`, embeddings `gemini-embedding-001`)
- **LLM usage:** 28 generate calls {'plan': 1, 'write': 12, 'patch': 3, 'judge': 12}, 16 embed calls, 97,153 input / 27,080 output tokens, 5 retries
- **Counts:** planned=12, written=12, revisions=0, patches=3, passed=12, quarantined=0, failed=0, skipped_unchanged=0, merged=0, retired=0, sweep_requeued=0
- **Time per stage (s):** ingest+cluster 0.8, plan 49.0, write+check 182.3, sweep 0.0, render 0.1
- **Workers:** 4 (siblings never in flight together) · thinking: {'planner': '', 'writer_draft': 'low', 'writer_revise': '', 'patch': 'low', 'judge': 'low'}
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
| 5-word shingle Jaccard | 0.0151 | 0.0036 | 0.12 |
| Embedding cosine | 0.9298 | 0.8476 | 0.93 |
| Verbatim shared sentences (≥8 words) | 0 | 0.00 | 1 |
| FAQ question cosine (across pages) | 0.9227 | 0.7544 | 0.93 |

Most similar pairs:

| Page A | Page B | Cosine | Jaccard | Shared sentences |
|---|---|---|---|---|
| convert-scanned-image-to-text | ocr-handwriting-iphone | 0.9298 | 0.0151 | 0 |
| best-free-pdf-scanner-app | scan-document-without-an-app | 0.9293 | 0.0059 | 0 |
| best-free-pdf-scanner-app | scan-receipt-on-iphone | 0.8962 | 0.0028 | 0 |
| best-free-pdf-scanner-app | remove-shadow-from-scanned-document | 0.8927 | 0.0036 | 0 |
| best-free-pdf-scanner-app | convert-scanned-image-to-text | 0.8926 | 0.0025 | 0 |
| scan-document-without-an-app | scan-receipt-on-iphone | 0.8905 | 0.0022 | 0 |
| best-free-pdf-scanner-app | ocr-handwriting-iphone | 0.8856 | 0.0011 | 0 |
| ocr-handwriting-iphone | scan-document-without-an-app | 0.8847 | 0.0011 | 0 |

## Pages

Closest neighbour = the most similar other published page in the final corpus.

| Page | Status | Words | Drafts | Editor (spec/use/acc/swap) | Max Jaccard | Max cosine (closest page) |
|---|---|---|---|---|---|---|
| best-free-pdf-scanner-app | passed | 939 | 1 | 5/5/5/pass | 0.0059 | 0.9293 (scan-document-without-an-app) |
| combine-multiple-pdfs-into-one | passed | 891 | 1 | 5/5/5/pass | 0.0084 | 0.8752 (best-free-pdf-scanner-app) |
| compress-pdf-file-size-free | passed | 961 | 1 | 5/5/5/pass | 0.0084 | 0.8629 (combine-multiple-pdfs-into-one) |
| convert-pdf-to-word-free | passed | 1068 | 1 | 5/5/5/pass | 0.0086 | 0.8821 (convert-scanned-image-to-text) |
| convert-scanned-image-to-text | passed | 1095 | 2 | 5/5/5/pass | 0.0151 | 0.9298 (ocr-handwriting-iphone) |
| ocr-handwriting-iphone | passed | 878 | 1 | 5/5/5/pass | 0.0151 | 0.9298 (convert-scanned-image-to-text) |
| remove-shadow-from-scanned-document | passed | 1029 | 2 | 5/5/5/pass | 0.0064 | 0.8927 (best-free-pdf-scanner-app) |
| scan-business-card-to-contacts | passed | 927 | 1 | 5/5/5/pass | 0.0096 | 0.8657 (convert-scanned-image-to-text) |
| scan-document-without-an-app | passed | 981 | 1 | 5/5/5/pass | 0.0059 | 0.9293 (best-free-pdf-scanner-app) |
| scan-passport-photo-for-application | passed | 882 | 1 | 5/5/5/pass | 0.0029 | 0.8349 (scan-document-without-an-app) |
| scan-receipt-on-iphone | passed | 887 | 2 | 5/5/5/pass | 0.0072 | 0.8962 (best-free-pdf-scanner-app) |
| sign-pdf-on-iphone-free | passed | 957 | 1 | 5/5/5/pass | 0.0081 | 0.8797 (scan-document-without-an-app) |

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

**Run #9** (ok, started 2026-10-11 09:18 UTC)

- **scan-receipt-on-iphone** [qa/warning]: attempt 1 (draft): banned filler phrase: 'seamless'
- **remove-shadow-from-scanned-document** [qa/warning]: attempt 1 (draft): FAQ 'Does the built-in iOS Notes or Files scanner have a shadow removal tool?' asks the same thing as /scan-document-without-an-app's 'Can built-in iOS scanning remove shadows or compress large PDFs?' (cosine 0.931); link to that page instead and ask a question specific to this page
- **convert-scanned-image-to-text** [qa/warning]: attempt 1 (draft): FAQ 'Can I copy the recognised text or export it as a .txt file for free?' duplicates /ocr-handwriting-iphone's FAQ 'Can I export recognised handwritten notes as a text file for free?'; replace it with a question only this page's searcher would ask; FAQ 'Can I copy the recognised text or export it as a .txt file for free?' asks the same thing as /ocr-handwriting-iphone's 'Can I export recognised handwritten notes as a text file for free?' (cosine 0.952); link to that page instead and ask a question specific to this page

