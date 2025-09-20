import os
import re
import sys
import subprocess
import multiprocessing
import time
import json
import csv
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Tuple, Optional, Dict

def check_and_install_dependencies():
    """Check if required packages are installed and install them if missing."""
    required_packages = {
        'PyPDF2': 'PyPDF2>=3.0.1',
        'pdfplumber': 'pdfplumber>=0.10.3',
        'Pillow': 'Pillow>=10.1.0'
    }
    
    missing_packages = []
    
    for package, pip_name in required_packages.items():
        try:
            __import__(package)
        except ImportError:
            missing_packages.append(pip_name)
    
    if missing_packages:
        for package in missing_packages:
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install', package], 
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"Installed {package}")
            except subprocess.CalledProcessError as e:
                print(f"Failed to install {package}: {e}")
                sys.exit(1)

check_and_install_dependencies()

import PyPDF2
import pdfplumber

class PDFProcessor:
    def __init__(self, input_pdf_path: str, output_dir: Optional[str] = None):
        self.input_pdf_path = Path(input_pdf_path)
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            pdf_name = self.input_pdf_path.stem
            self.output_dir = self.input_pdf_path.parent / f"split pdfs {pdf_name}"
        self.output_dir.mkdir(exist_ok=True)
        
    def extract_appeal_number_from_page(self, page) -> Optional[str]:
        try:
            text = page.extract_text()
            if not text:
                return None
            
            appeal_pattern = r'Appeal\s+No\.\s*([A-Za-z0-9\-]+)'
            match = re.search(appeal_pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
            
            fallback_pattern = r'Appeal\s+([A-Za-z0-9\-]+)'
            fallback_match = re.search(fallback_pattern, text, re.IGNORECASE)
            if fallback_match:
                return fallback_match.group(1).strip()
                
            return None
            
        except Exception as e:
            print(f"Error extracting appeal number from page: {e}")
            return None
    
    def extract_person_info_from_page(self, page) -> Dict[str, str]:
        """Extract person name and address from page text"""
        try:
            text = page.extract_text()
            if not text:
                return {"name": "", "address": ""}
            
            # Extract name patterns
            name_patterns = [
                r'Company/Applicant:\s*([^\n]+)',
                r'Applicant:\s*([^\n]+)',
                r'Name:\s*([^\n]+)',
                r'Property Owner:\s*([^\n]+)'
            ]
            
            name = ""
            for pattern in name_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    name = match.group(1).strip()
                    break
            
            # Extract address patterns
            address_patterns = [
                r'Property Location:\s*([^\n]+)',
                r'Address:\s*([^\n]+)',
                r'Property Address:\s*([^\n]+)',
                r'Location:\s*([^\n]+)'
            ]
            
            address = ""
            for pattern in address_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    address = match.group(1).strip()
                    break
            
            return {"name": name, "address": address}
            
        except Exception as e:
            print(f"Error extracting person info from page: {e}")
            return {"name": "", "address": ""}
    
    def process_single_pdf(self) -> Tuple[bool, int, str, List[Dict]]:
        """Process a single PDF and return (success, pages_processed, output_dir, csv_data)"""
        csv_data = []
        try:
            with open(self.input_pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                total_pages = len(pdf_reader.pages)
                
                with pdfplumber.open(self.input_pdf_path) as pdf_plumber:
                    for page_num in range(total_pages):
                        appeal_number = self.extract_appeal_number_from_page(pdf_plumber.pages[page_num])
                        person_info = self.extract_person_info_from_page(pdf_plumber.pages[page_num])
                        
                        if not appeal_number:
                            appeal_number = "UNKNOWN"
                        
                        pdf_writer = PyPDF2.PdfWriter()
                        pdf_writer.add_page(pdf_reader.pages[page_num])
                        
                        filename = f"Case_Number_{appeal_number}_Page_Num_{page_num + 1}.pdf"
                        output_path = self.output_dir / filename
                        
                        with open(output_path, 'wb') as output_file:
                            pdf_writer.write(output_file)
                        
                        # Add to CSV data
                        csv_data.append({
                            'filename': filename,
                            'case_number': appeal_number,
                            'name': person_info['name'],
                            'address': person_info['address'],
                            'source_file': self.input_pdf_path.name,
                            'page_number': page_num + 1
                        })
                
                return True, total_pages, str(self.output_dir), csv_data
                
        except Exception as e:
            print(f"Error processing {self.input_pdf_path}: {e}")
            return False, 0, str(self.output_dir), []

def process_pdf_worker(pdf_path: str) -> Tuple[str, bool, int, str, List[Dict]]:
    """Worker function for multiprocessing"""
    processor = PDFProcessor(pdf_path)
    success, pages, output_dir, csv_data = processor.process_single_pdf()
    return pdf_path, success, pages, output_dir, csv_data

def save_progress(completed_files: List[str], failed_files: List[str], progress_file: str):
    """Save progress to resume later if needed"""
    progress = {
        'completed': completed_files,
        'failed': failed_files,
        'timestamp': time.time()
    }
    with open(progress_file, 'w') as f:
        json.dump(progress, f, indent=2)

def load_progress(progress_file: str) -> Tuple[List[str], List[str]]:
    """Load previous progress if resuming"""
    if os.path.exists(progress_file):
        with open(progress_file, 'r') as f:
            progress = json.load(f)
        return progress.get('completed', []), progress.get('failed', [])
    return [], []

def save_csv_data(all_csv_data: List[Dict], csv_filename: str = "pdf_processing_results.csv"):
    """Save all CSV data to a file"""
    if not all_csv_data:
        return
    
    fieldnames = ['filename', 'case_number', 'name', 'address', 'source_file', 'page_number']
    
    with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_csv_data)
    
    print(f"CSV data saved to: {csv_filename}")

def process_pdfs_enterprise(pdf_paths: List[str], max_workers: Optional[int] = None, 
                          resume: bool = False, progress_file: str = "processing_progress.json"):
    """Enterprise-grade PDF processing with multiprocessing and progress tracking"""
    
    if max_workers is None:
        max_workers = min(multiprocessing.cpu_count(), 8)  # Cap at 8 to avoid memory issues
    
    print(f"Processing {len(pdf_paths)} PDFs with {max_workers} workers...")
    
    completed_files = []
    failed_files = []
    all_csv_data = []
    
    if resume:
        completed_files, failed_files = load_progress(progress_file)
        remaining_files = [f for f in pdf_paths if f not in completed_files and f not in failed_files]
        print(f"Resuming: {len(completed_files)} completed, {len(failed_files)} failed, {len(remaining_files)} remaining")
        pdf_paths = remaining_files
    
    start_time = time.time()
    total_pages = 0
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_pdf = {executor.submit(process_pdf_worker, pdf_path): pdf_path for pdf_path in pdf_paths}
        
        for future in as_completed(future_to_pdf):
            pdf_path = future_to_pdf[future]
            try:
                pdf_path_result, success, pages, output_dir, csv_data = future.result()
                
                if success:
                    completed_files.append(pdf_path_result)
                    total_pages += pages
                    all_csv_data.extend(csv_data)
                    print(f"Completed: {Path(pdf_path_result).name} ({pages} pages) -> {output_dir}")
                else:
                    failed_files.append(pdf_path_result)
                    print(f"Failed: {Path(pdf_path_result).name}")
                
                # Save progress every 10 files
                if len(completed_files + failed_files) % 10 == 0:
                    save_progress(completed_files, failed_files, progress_file)
                    
            except Exception as e:
                failed_files.append(pdf_path)
                print(f"Exception processing {pdf_path}: {e}")
    
    # Save CSV data
    save_csv_data(all_csv_data)
    
    # Final progress save
    save_progress(completed_files, failed_files, progress_file)
    
    end_time = time.time()
    duration = end_time - start_time
    
    print(f"\n{'='*60}")
    print(f"PROCESSING COMPLETE")
    print(f"{'='*60}")
    print(f"Total files processed: {len(completed_files + failed_files)}")
    print(f"Successful: {len(completed_files)}")
    print(f"Failed: {len(failed_files)}")
    print(f"Total pages processed: {total_pages}")
    print(f"Processing time: {duration:.2f} seconds")
    print(f"Average time per file: {duration/len(completed_files + failed_files):.2f} seconds")
    print(f"Pages per second: {total_pages/duration:.2f}")
    print(f"CSV records created: {len(all_csv_data)}")
    
    if failed_files:
        print(f"\nFailed files:")
        for failed in failed_files:
            print(f"  - {failed}")
    
    return len(completed_files), len(failed_files)

def main():
    if len(sys.argv) < 2:
        print("Usage: python pdf_scanner_enterprise.py <pdf1> <pdf2> ... [--workers N] [--resume]")
        print("Examples:")
        print("  python pdf_scanner_enterprise.py *.pdf")
        print("  python pdf_scanner_enterprise.py *.pdf --workers 16")
        print("  python pdf_scanner_enterprise.py *.pdf --resume")
        sys.exit(1)
    
    pdf_files = []
    max_workers = None
    resume = False
    
    for arg in sys.argv[1:]:
        if arg == '--resume':
            resume = True
        elif arg.startswith('--workers='):
            max_workers = int(arg.split('=')[1])
        elif arg.endswith('.pdf'):
            pdf_files.append(arg)
        else:
            print(f"Warning: '{arg}' is not a PDF file, skipping...")
    
    if not pdf_files:
        print("Error: No PDF files provided!")
        sys.exit(1)
    
    # Use glob pattern if provided
    if len(sys.argv) == 2 and '*' in sys.argv[1]:
        import glob
        pdf_files = glob.glob(sys.argv[1])
    
    print(f"Found {len(pdf_files)} PDF files to process")
    
    successful, failed = process_pdfs_enterprise(pdf_files, max_workers, resume)
    
    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()

