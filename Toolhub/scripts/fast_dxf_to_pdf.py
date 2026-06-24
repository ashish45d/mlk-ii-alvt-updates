# -*- coding: utf-8 -*-
"""
Fast DXF to PDF Standalone Tool (Parallel Edition)
Uses ezdxf + PyMuPDF for high-performance, vectorized PDF generation.
Supports multi-core parallel processing and PDF consolidation (merging).
"""
import sys
import os
import re
import time
import ezdxf
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
from ezdxf.addons.drawing.config import Configuration, ColorPolicy, BackgroundPolicy
from ezdxf.addons.drawing import layout
from concurrent.futures import ProcessPoolExecutor, as_completed

def natural_sort_key(s):
    """
    Key for natural sorting (e.g., Page 2 before Page 10).
    """
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', s)]

def consolidate_pdfs(output_dir, final_pdf_name="CONSOLIDATED_DRAWINGS.pdf"):
    """
    Merge all PDF files in the output directory into a single PDF in numeric order.
    """
    import pymupdf
    print(f"\nConsolidating PDFs in '{output_dir}'...")
    
    pdf_files = [f for f in os.listdir(output_dir) if f.lower().endswith(".pdf") and f != final_pdf_name]
    if not pdf_files:
        print("  [ERROR] No PDF files found to consolidate.")
        return
        
    # Sort files naturally
    pdf_files.sort(key=natural_sort_key)
    
    merged_doc = pymupdf.open()
    count = 0
    for f in pdf_files:
        f_path = os.path.join(output_dir, f)
        try:
            with pymupdf.open(f_path) as doc:
                merged_doc.insert_pdf(doc)
            count += 1
            if count % 50 == 0:
                print(f"  Merged {count}/{len(pdf_files)} files...")
        except Exception as e:
            print(f"  [WARNING] Failed to merge {f}: {e}")
            
    final_path = os.path.join(output_dir, final_pdf_name)
    merged_doc.save(final_path)
    merged_doc.close()
    
    print(f"  [SUCCESS] Created consolidated file: {final_pdf_name}")
    print(f"  Total pages merged: {len(pdf_files)}")

def _worker_convert(args):
    """
    Isolated worker function for parallel processing.
    """
    dxf_path, pdf_path, use_color, index, total = args
    filename = os.path.basename(dxf_path)
    
    try:
        start_time = time.time()
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        ctx = RenderContext(doc)
        
        backend = PyMuPdfBackend()
        cfg = Configuration(
            color_policy=ColorPolicy.COLOR if use_color else ColorPolicy.BLACK,
            background_policy=BackgroundPolicy.CUSTOM,
            custom_bg_color="#FFFFFF"
        )
        
        frontend = Frontend(ctx, backend, config=cfg)
        frontend.draw_layout(msp, finalize=True)
        
        page = layout.Page(0, 0, layout.Units.mm, margins=layout.Margins.all(10), max_width=420, max_height=297)
        settings = layout.Settings(fit_page=True, page_alignment=layout.PageAlignment.MIDDLE_CENTER)
        
        pdf_bytes = backend.get_pdf_bytes(page, settings=settings)
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
            
        elapsed = time.time() - start_time
        return True, filename, elapsed
    except Exception as e:
        return False, filename, str(e)

def batch_process_parallel(source_dir, output_dir, use_color=True, merge=False, cancel_check=None):
    """
    Process all DXF files in a directory using multiple CPU cores and optionally merge.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    dxf_files = sorted([f for f in os.listdir(source_dir) if f.lower().endswith(".dxf")])
    total_files = len(dxf_files)
    
    if total_files == 0:
        print(f"No DXF files found in {source_dir}")
        return

    print(f"Parallel Batch Mode: Found {total_files} DXF files.")
    num_workers = int(os.environ.get("NUMBER_OF_PROCESSORS", os.cpu_count() or 4))
    print(f"Utilizing {num_workers} CPU cores for parallel conversion...")
    
    tasks = []
    for i, f in enumerate(dxf_files, 1):
        in_path = os.path.join(source_dir, f)
        out_path = os.path.join(output_dir, f.lower().replace(".dxf", ".pdf"))
        tasks.append((in_path, out_path, use_color, i, total_files))
    
    start_batch = time.time()
    success_count = 0
    
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        future_to_file = {executor.submit(_worker_convert, task): task[1] for task in tasks}
        
        total_done = 0
        for future in as_completed(future_to_file):
            # Check for cancellation
            if cancel_check and cancel_check():
                print("\n[CANCEL] Cancellation requested. Shutting down workers...")
                executor.shutdown(wait=False, cancel_futures=True)
                return False

            total_done += 1
            success, filename, result = future.result()
            if success:
                success_count += 1
                print(f"[{total_done}/{total_files}] Completed {filename}", flush=True)
            else:
                print(f"  [FAILED] {filename}: {result}", flush=True)
                
    total_elapsed = time.time() - start_batch
    print(f"\nBatch Complete: {success_count} of {total_files} files converted.")
    print(f"Total Time: {total_elapsed:.2f}s (Average: {total_elapsed/total_files:.2f}s per file)")
    
    if merge:
        consolidate_pdfs(output_dir)
    return True

def export_dxf_list_to_pdf(dxf_paths, pdf_path, use_color=True, cancel_check=None):
    """
    Export exactly the supplied list of DXF file paths into a single merged PDF.
    Uses a temporary directory to isolate files and avoid directory scans.
    """
    if not dxf_paths:
        print("No DXF paths provided for PDF export.", flush=True)
        return False
        
    import tempfile
    import shutil
    
    tmp_dir = tempfile.mkdtemp(prefix='alvt_pdf_')
    try:
        # Copy only the specified DXF files to the temp dir
        copied_paths = []
        for f in dxf_paths:
            if os.path.exists(f):
                dst = os.path.join(tmp_dir, os.path.basename(f))
                shutil.copy2(f, dst)
                copied_paths.append(dst)
                
        if not copied_paths:
            print("No valid DXF files found for PDF export.", flush=True)
            return False
            
        # Run parallel batch conversion on the temp folder
        batch_process_parallel(tmp_dir, tmp_dir, use_color=use_color, merge=True, cancel_check=cancel_check)
        
        gen = os.path.join(tmp_dir, 'CONSOLIDATED_DRAWINGS.pdf')
        if os.path.exists(gen):
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except Exception as e:
                    print(f"Could not remove existing PDF {pdf_path}: {e}", flush=True)
            shutil.move(gen, pdf_path)
            print(f"Successfully created consolidated PDF: {pdf_path}", flush=True)
            return True
        else:
            print("Consolidated PDF was not generated by the engine.", flush=True)
            return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    import argparse
    parser = argparse.ArgumentParser(description="Parallel High-Speed DXF to PDF Converter")
    parser.add_argument("input", help="Input DXF file path or Directory path")
    parser.add_argument("-o", "--output", help="Output PDF file path or Directory path")
    parser.add_argument("--mono", action="store_true", help="Use Monochrome (Black & White) mode")
    parser.add_argument("--merge", action="store_true", help="Consolidate all generated PDFs into one file")
    
    args = parser.parse_args()
    
    source = args.input
    output = args.output
    use_color = not args.mono
    
    if os.path.isdir(source):
        # Batch Mode
        out_target = output or os.path.join(source, "PDF_EXPORT")
        batch_process_parallel(source, out_target, use_color, merge=args.merge)
    else:
        # Single File Mode - only for convenience
        ret = _worker_convert((source, output or source.lower().replace(".dxf", ".pdf"), use_color, 1, 1))
        if ret[0]:
            print(f"Successfully converted {ret[1]} in {ret[2]:.2f}s")
        else:
            print(f"Failed to convert {ret[1]}: {ret[2]}")
