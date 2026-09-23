import re
import os

log_file = "processed_urls.log"
output_file = "completed_urls.txt"

completed_urls = set()

if os.path.exists(log_file):
    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "Successfully uploaded and renamed:" in line:
                # The log line format looks like:
                # Successfully uploaded and renamed: filename.mp4 (Original URL: https://example.com/...)
                match = re.search(r"\(Original URL:\s*(.+?)\)", line)
                if match:
                    url = match.group(1).strip()
                    completed_urls.add(url)

    # Read existing completed urls if the file already exists
    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            for line in f:
                url = line.strip()
                if url:
                    completed_urls.add(url)

    with open(output_file, "w", encoding="utf-8") as f:
        for url in sorted(completed_urls):
            f.write(url + "\n")
            
    print(f"Extracted {len(completed_urls)} successfully processed URLs into {output_file}")
else:
    print(f"{log_file} not found.")
