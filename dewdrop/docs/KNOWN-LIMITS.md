# Known Technical Boundaries & Fidelity Limits

1. **PDF to Word (`.docx`)**:
   - Designed for text-based PDFs. Scanned PDFs that contain only page-sized raster images without an underlying text layer are detected and rejected with a clear message (`No text layer detected in PDF`) because OCR is intentionally excluded to keep the application small and offline.
   - Preserves headings (inferred from font size and uppercase/weight cues), paragraphs, bulleted lists, simple pipe/grid tables, and embedded images. Complex multi-layer magazine layouts, floating sidebars, and custom embedded vector curves are flattened into sequential document blocks.
2. **Word (`.docx`) to PDF**:
   - Accepts modern `.docx` files only (legacy binary `.doc` files and macro-enabled `.docm` / `.docx` files containing `vbaProject.bin` are refused for security).
   - Renders headings, body paragraphs, inline emphasis, bulleted lists, tables, page margins, and embedded raster images using system fonts from the Windows Fonts folder. Advanced Word features such as Track Changes balloons, SmartArt, embedded OLE objects, and floating text boxes are not rendered.
3. **File & Batch Safety Ceilings**:
   - Maximum batch size: **100 files** per operation.
   - Standard file input ceiling: **100 MiB** (streamed archive, audio, and video inputs allow up to **2 GiB**).
   - Maximum image pixel count: **40 megapixels** per image (larger images are refused prior to frame buffer allocation).
   - Maximum combined PDF length: **200 pages**; encrypted/password-protected PDFs and archives are refused.
   - Maximum output size per job: **512 MiB**; archive extraction is capped at **2,000 files** and **512 MiB** total uncompressed output, with maximum directory nesting of **20 levels**.
4. **Shell Drag Detection Scope**:
   - Reacts exclusively to file drags originating from Windows Explorer item views, the Desktop, and standard Open/Save file dialogs (`CF_HDROP`). Dragging raw text snippets, browser URLs, or custom private clipboards from third-party applications is ignored by design.
